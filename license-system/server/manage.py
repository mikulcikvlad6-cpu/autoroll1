"""CLI для ручного управления без Discord и админ-панели.

Примеры:
    python manage.py issue 123456789012345678 --note "покупка"
    python manage.py off                 # глобальный kill switch
    python manage.py ban ABCDEF... --reason "слив"
    python manage.py list
"""
from __future__ import annotations

import argparse
import sys

from app.database import SessionLocal, init_db
from app.services import (
    add_blacklist,
    app_enabled,
    find_license,
    get_settings_map,
    issue_license,
    remove_blacklist,
    reset_hwid,
    set_active,
    set_setting,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoRoll license admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="создать таблицы")
    p = sub.add_parser("issue", help="выдать ключ")
    p.add_argument("discord_id", type=int)
    p.add_argument("--note", default="cli")
    p.add_argument("--hwid", default=None)

    sub.add_parser("list", help="список ключей")
    p = sub.add_parser("info", help="информация о ключе")
    p.add_argument("key")
    p = sub.add_parser("revoke")
    p.add_argument("key")
    p = sub.add_parser("unrevoke")
    p.add_argument("key")
    p = sub.add_parser("reset-hwid")
    p.add_argument("key")
    p = sub.add_parser("ban")
    p.add_argument("hwid")
    p.add_argument("--reason", default="manual")
    p = sub.add_parser("unban")
    p.add_argument("hwid")
    p = sub.add_parser("on", help="включить клиент глобально")
    p = sub.add_parser("off", help="KILL SWITCH: выключить клиент глобально")
    p = sub.add_parser("delay")
    p.add_argument("ms", type=int)
    sub.add_parser("settings")

    args = parser.parse_args()
    init_db()

    with SessionLocal() as db:
        if args.cmd == "init":
            print("OK: таблицы созданы")
        elif args.cmd == "issue":
            lic, created = issue_license(db, args.discord_id, args.note, args.hwid)
            print(("Создан: " if created else "Уже есть: ") + lic.key)
        elif args.cmd == "list":
            from sqlalchemy import select

            from app.models import License

            for lic in db.scalars(select(License).order_by(License.created_at.desc())):
                d = lic.as_dict()
                print(
                    f"{d['key']:<14} {d['discord_id']:<20} "
                    f"active={str(d['active']):<5} hwid={d['hwid'] or '—'}"
                )
        elif args.cmd == "info":
            lic = find_license(db, args.key)
            if not lic:
                print("Ключ не найден", file=sys.stderr)
                return 1
            for k, v in lic.as_dict().items():
                print(f"{k:<14} {v}")
        elif args.cmd == "revoke":
            lic = find_license(db, args.key)
            if not lic:
                print("Ключ не найден", file=sys.stderr)
                return 1
            set_active(db, lic, False)
            print("Отозван")
        elif args.cmd == "unrevoke":
            lic = find_license(db, args.key)
            if not lic:
                print("Ключ не найден", file=sys.stderr)
                return 1
            set_active(db, lic, True)
            print("Активен")
        elif args.cmd == "reset-hwid":
            lic = find_license(db, args.key)
            if not lic:
                print("Ключ не найден", file=sys.stderr)
                return 1
            reset_hwid(db, lic, "cli")
            print("HWID сброшен")
        elif args.cmd == "ban":
            add_blacklist(db, args.hwid, args.reason, "cli")
            print("HWID забанен")
        elif args.cmd == "unban":
            print("Удалён" if remove_blacklist(db, args.hwid) else "Не найден")
        elif args.cmd == "on":
            set_setting(db, "app_enabled", "1")
            print("Клиент включён")
        elif args.cmd == "off":
            set_setting(db, "app_enabled", "0")
            print("KILL SWITCH активирован: клиенты получат команду на остановку")
        elif args.cmd == "delay":
            set_setting(db, "click_delay_ms", str(args.ms))
            print(f"Задержка {args.ms} мс")
        elif args.cmd == "settings":
            enabled = app_enabled(db)
            for k, v in get_settings_map(db).items():
                print(f"{k:<22} {v}")
            print(f"{'app_enabled(итог)':<22} {enabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
