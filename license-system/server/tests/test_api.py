"""Сквозные тесты API: выдача ключа -> активация -> проверка -> отзыв -> бан."""
from __future__ import annotations

import hmac
import hashlib
import json

from conftest import ADMIN_HEADERS

SECRET = "test-client-secret"
HWID = "ABCDEF0123456789ABCDEF0123456789"
HWID2 = "11112222333344445555666677778888"


def _sign(data: dict) -> str:
    body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def _assert_signed(payload: dict) -> None:
    body = {k: v for k, v in payload.items() if k not in ("sig", "server_time")}
    assert hmac.compare_digest(_sign(body), payload["sig"]), "ответ не подписан"


def issue(client, discord_id: str = "123456789012345678") -> str:
    response = client.post(
        "/admin/licenses",
        json={"discord_id": discord_id, "note": "test"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["license"]["key"]


def activate(client, key: str, hwid: str = HWID, discord_id: str | None = None):
    body = {"key": key, "hwid": hwid}
    if discord_id:
        body["discord_id"] = discord_id
    return client.post("/api/v1/activate", json=body)


def test_health(client):
    assert client.get("/api/v1/health").json()["ok"] is True


def test_full_license_flow(client):
    key = issue(client)
    assert key.startswith("LARP-")

    response = activate(client, key)
    assert response.status_code == 200, response.text
    payload = response.json()
    _assert_signed(payload)
    token = payload["token"]
    assert payload["license"]["hwid"] == HWID

    verify = client.post("/api/v1/verify", json={"token": token, "hwid": HWID})
    assert verify.status_code == 200
    data = verify.json()
    _assert_signed(data)
    assert data["enabled"] is True
    assert data["click_delay_ms"] == 2000

    # повторная активация того же ПК не ломается
    assert activate(client, key).status_code == 200


def test_key_is_unique_per_discord_account(client):
    first = issue(client, "111111111111111111")
    second = issue(client, "111111111111111111")
    assert first == second  # идемпотентно

    other = issue(client, "222222222222222222")
    assert other != first


def test_hwid_locked_to_first_pc(client):
    key = issue(client)
    assert activate(client, key).status_code == 200

    response = activate(client, key, hwid=HWID2)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "HWID_MISMATCH"


def test_admin_reset_hwid_allows_new_pc(client):
    key = issue(client)
    assert activate(client, key).status_code == 200

    reset = client.post(f"/admin/licenses/{key}/reset-hwid", headers=ADMIN_HEADERS)
    assert reset.status_code == 200

    assert activate(client, key, hwid=HWID2).status_code == 200


def test_revoked_key_stops_client(client):
    key = issue(client)
    token = activate(client, key).json()["token"]

    client.post(
        f"/admin/licenses/{key}/active",
        json={"active": False},
        headers=ADMIN_HEADERS,
    )

    data = client.post("/api/v1/verify", json={"token": token, "hwid": HWID}).json()
    assert data["enabled"] is False
    assert data["code"] == "KEY_REVOKED"

    assert activate(client, key).status_code == 403


def test_killswitch_disables_everyone(client):
    key = issue(client)
    token = activate(client, key).json()["token"]

    kill = client.post(
        "/admin/killswitch", json={"enabled": False}, headers=ADMIN_HEADERS
    )
    assert kill.status_code == 200

    data = client.post("/api/v1/verify", json={"token": token, "hwid": HWID}).json()
    assert data["enabled"] is False
    assert data["code"] == "DISABLED_BY_ADMIN"
    assert data["kill"] is True

    # новая активация тоже не проходит
    assert activate(client, key).status_code == 200  # активация разрешена
    data2 = client.post("/api/v1/verify", json={"token": token, "hwid": HWID}).json()
    assert data2["enabled"] is False

    client.post("/admin/killswitch", json={"enabled": True}, headers=ADMIN_HEADERS)
    data3 = client.post("/api/v1/verify", json={"token": token, "hwid": HWID}).json()
    assert data3["enabled"] is True


def test_blacklisted_hwid_gets_blocked(client):
    key = issue(client)
    assert activate(client, key).status_code == 200

    client.post(
        "/admin/blacklist",
        json={"hwid": HWID, "reason": "слив"},
        headers=ADMIN_HEADERS,
    )

    response = activate(client, key)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "HWID_BANNED"


def test_token_from_other_pc_rejected(client):
    key = issue(client)
    token = activate(client, key).json()["token"]

    response = client.post("/api/v1/verify", json={"token": token, "hwid": HWID2})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "TOKEN_HWID_MISMATCH"


def test_tamper_event_visible_to_admin(client):
    response = client.post(
        "/api/v1/tamper",
        json={"hwid": HWID, "kind": "debugger", "detail": "x64dbg"},
    )
    assert response.status_code == 200
    _assert_signed(response.json())

    events = client.get("/admin/events?kind=TAMPER", headers=ADMIN_HEADERS).json()
    assert events["items"]
    assert events["items"][0]["kind"] == "TAMPER"


def test_admin_requires_key(client):
    assert client.get("/admin/overview").status_code == 403
    assert client.get("/admin/overview", headers={"X-Admin-Key": "nope"}).status_code == 403
    assert client.get("/admin/overview", headers=ADMIN_HEADERS).status_code == 200


def test_bad_key_returns_404(client):
    response = activate(client, "LARP-000000")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "KEY_NOT_FOUND"
