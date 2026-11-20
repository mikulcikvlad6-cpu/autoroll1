"""Живой смоук-тест: поднимает все сценарии против реального сервера.

    python tools/smoke_client.py --api http://127.0.0.1:8000 \
        --secret <CLIENT_SECRET> --admin <ADMIN_API_KEY>

Скрипт сам выдаёт ключ через админ-API, активирует его как клиент, дёргает
kill switch и бан, и проверяет, что клиент реагирует правильно.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

from license_client import LicenseClient, LicenseError  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))
    print(f"[{PASS if condition else FAIL}] {name} {detail}")


def admin_call(api: str, admin_key: str, method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Admin-Key": admin_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--secret", required=True)
    parser.add_argument("--admin", required=True)
    parser.add_argument("--discord-id", default="900000000000000001")
    parser.add_argument("--hwid", default="SMOKETEST0123456789ABCDEF012345")
    parser.add_argument("--cache", default="")
    args = parser.parse_args()

    api, admin_key = args.api, args.admin
    hwid = args.hwid

    # 1. health
    status, body = admin_call(api, admin_key, "GET", "/api/v1/health")
    check("health", status == 200 and body.get("ok") is True)

    # 2. выдача ключа
    status, body = admin_call(
        api, admin_key, "POST", "/admin/licenses", {"discord_id": args.discord_id, "note": "smoke"}
    )
    key = body.get("license", {}).get("key", "")
    check("issue key", status == 200 and key.startswith("LARP-"), key)

    client = LicenseClient(
        api_base=api,
        secret=args.secret,
        hwid=hwid,
        owner_id=args.discord_id,
        cache_file=args.cache or str(Path(__file__).with_name("smoke_cache.dat")),
    )

    # 3. активация
    try:
        data = client.activate(key)
        check("activate", data.get("ok") is True and bool(data.get("token")))
    except Exception as exc:
        check("activate", False, repr(exc))
        return 1

    # 4. проверка + подпись уже проверена внутри клиента
    data = client.verify()
    check("verify enabled", data.get("enabled") is True)
    check("click delay", int(data.get("click_delay_ms", 0)) > 0)

    # 5. kill switch off
    admin_call(api, admin_key, "POST", "/admin/killswitch", {"enabled": False})
    data = client.verify()
    check(
        "kill switch stops client",
        data.get("enabled") is False and data.get("code") == "DISABLED_BY_ADMIN",
        str(data.get("code")),
    )
    admin_call(api, admin_key, "POST", "/admin/killswitch", {"enabled": True})
    check("kill switch restored", client.verify().get("enabled") is True)

    # 6. отзыв ключа
    admin_call(api, admin_key, "POST", f"/admin/licenses/{key}/active", {"active": False})
    data = client.verify()
    check("revoked key", data.get("enabled") is False and data.get("code") == "KEY_REVOKED")
    admin_call(api, admin_key, "POST", f"/admin/licenses/{key}/active", {"active": True})

    # 7. бан HWID
    admin_call(api, admin_key, "POST", "/admin/blacklist", {"hwid": hwid, "reason": "smoke"})
    try:
        client.activate(key)
        check("blacklist blocks activate", False)
    except LicenseError as exc:
        check("blacklist blocks activate", exc.code == "HWID_BANNED", exc.code)

    data = client.verify()
    check(
        "blacklist blocks verify",
        data.get("enabled") is False and data.get("code") == "HWID_BANNED",
    )
    admin_call(api, admin_key, "DELETE", f"/admin/blacklist/{hwid}")

    # 8. сигнал о взломе
    client.report_tamper("smoke", "проверка канала событий")
    time.sleep(0.3)
    status, body = admin_call(api, admin_key, "GET", "/admin/events?kind=TAMPER")
    check("tamper event stored", status == 200 and any(
        event.get("detail", "").startswith("smoke") for event in body.get("items", [])
    ))

    # 9. HWID другого ПК
    other = LicenseClient(
        api_base=api,
        secret=args.secret,
        hwid="OTHERPC0000000000000000000000000",
        cache_file=str(Path(__file__).with_name("smoke_cache2.dat")),
    )
    try:
        other.activate(key)
        check("hwid lock", False)
    except LicenseError as exc:
        check("hwid lock", exc.code == "HWID_MISMATCH", exc.code)

    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print(f"ПРОВАЛЕНО {len(failed)}: {', '.join(failed)}")
        return 1
    print(f"ВСЁ ОК: {len(results)} проверок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
