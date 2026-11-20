"""Бизнес-логика: лицензии, HWID, чёрный список, события, настройки.

Используется и HTTP-сервером, и Discord-ботом — поэтому это обычные функции,
а не зависимости FastAPI.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import DEFAULT_SETTINGS, Event, HwidBlacklist, License, Setting, utcnow


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------
def get_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key, "")


def get_settings_map(db: Session) -> dict[str, str]:
    values = dict(DEFAULT_SETTINGS)
    for row in db.scalars(select(Setting)):
        values[row.key] = row.value
    return values


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()


def app_enabled(db: Session) -> bool:
    return get_setting(db, "app_enabled") in ("1", "true", "True", "yes")


# --------------------------------------------------------------------------
# Ключи
# --------------------------------------------------------------------------
def normalize_key(raw: str) -> str:
    key = (raw or "").strip().upper().replace(" ", "")
    return key


def new_key(db: Session) -> str:
    for _ in range(50):
        key = f"{settings.key_prefix}-{secrets.token_hex(3).upper()}"
        if db.scalar(select(License.id).where(License.key == key)) is None:
            return key
    raise RuntimeError("Не удалось сгенерировать уникальный ключ")


def find_license(db: Session, key: str) -> License | None:
    return db.scalar(select(License).where(License.key == normalize_key(key)))


def find_by_discord(db: Session, discord_id: int) -> License | None:
    return db.scalar(select(License).where(License.discord_id == int(discord_id)))


def issue_license(
    db: Session,
    discord_id: int,
    note: str | None = None,
    hwid: str | None = None,
    build_id: str | None = None,
) -> tuple[License, bool]:
    """Возвращает (лицензия, создана_ли_новая). Повторный вызов идемпотентен."""
    existing = find_by_discord(db, discord_id)
    if existing is not None:
        return existing, False
    lic = License(
        key=new_key(db),
        discord_id=int(discord_id),
        note=note,
        hwid=hwid,
        build_id=build_id,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)
    log_event(db, "KEY_ISSUED", discord_id=lic.discord_id, key=lic.key, detail=note)
    return lic, True


def set_active(db: Session, lic: License, active: bool) -> License:
    lic.active = bool(active)
    db.commit()
    log_event(
        db,
        "KEY_ENABLED" if active else "KEY_REVOKED",
        discord_id=lic.discord_id,
        key=lic.key,
        hwid=lic.hwid,
    )
    return lic


def reset_hwid(db: Session, lic: License, by: str = "admin") -> License:
    lic.hwid = None
    lic.activated_at = None
    lic.hwid_resets = (lic.hwid_resets or 0) + 1
    db.commit()
    log_event(
        db,
        "HWID_RESET",
        discord_id=lic.discord_id,
        key=lic.key,
        detail=f"by={by}",
    )
    return lic


# --------------------------------------------------------------------------
# Чёрный список
# --------------------------------------------------------------------------
def is_blacklisted(db: Session, hwid: str | None) -> HwidBlacklist | None:
    if not hwid:
        return None
    return db.get(HwidBlacklist, hwid.upper())


def add_blacklist(
    db: Session, hwid: str, reason: str | None = None, by: str | None = None
) -> HwidBlacklist:
    hwid = hwid.strip().upper()
    row = db.get(HwidBlacklist, hwid)
    if row is None:
        row = HwidBlacklist(hwid=hwid, reason=reason, created_by=by)
        db.add(row)
    else:
        row.reason = reason or row.reason
    db.commit()
    db.refresh(row)
    log_event(db, "HWID_BANNED", hwid=hwid, detail=reason)
    return row


def remove_blacklist(db: Session, hwid: str) -> bool:
    hwid = hwid.strip().upper()
    row = db.get(HwidBlacklist, hwid)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    log_event(db, "HWID_UNBANNED", hwid=hwid)
    return True


# --------------------------------------------------------------------------
# События
# --------------------------------------------------------------------------
def log_event(
    db: Session,
    kind: str,
    *,
    discord_id: int | None = None,
    hwid: str | None = None,
    key: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> Event:
    event = Event(
        kind=kind,
        discord_id=int(discord_id) if discord_id else None,
        hwid=hwid.upper() if hwid else None,
        key=key,
        detail=detail,
        ip=ip,
    )
    db.add(event)
    db.commit()
    return event


# --------------------------------------------------------------------------
# Активация
# --------------------------------------------------------------------------
@dataclass
class ActivationResult:
    ok: bool
    code: str
    message: str
    lic: License | None = None


def activate(
    db: Session,
    key: str,
    hwid: str,
    discord_id: int | None = None,
    ip: str | None = None,
) -> ActivationResult:
    hwid = (hwid or "").strip().upper()
    if is_blacklisted(db, hwid):
        log_event(db, "BLOCKED_BLACKLIST", hwid=hwid, key=key, ip=ip)
        return ActivationResult(False, "HWID_BANNED", "Это устройство заблокировано.")

    if not hwid or len(hwid) < 8:
        return ActivationResult(False, "BAD_HWID", "Не удалось определить HWID.")

    lic = find_license(db, key)
    if lic is None:
        log_event(db, "ACTIVATE_FAIL", hwid=hwid, key=normalize_key(key), ip=ip)
        return ActivationResult(False, "KEY_NOT_FOUND", "Ключ не найден.")

    if not lic.active:
        log_event(db, "ACTIVATE_FAIL", key=lic.key, hwid=hwid, ip=ip, detail="revoked")
        return ActivationResult(False, "KEY_REVOKED", "Ключ отозван.")

    if discord_id and int(discord_id) != lic.discord_id:
        log_event(
            db, "ACTIVATE_FAIL", key=lic.key, hwid=hwid, ip=ip, detail="wrong account"
        )
        return ActivationResult(False, "WRONG_ACCOUNT", "Ключ принадлежит другому аккаунту.")

    if is_blacklisted(db, lic.hwid) and lic.hwid != hwid:
        return ActivationResult(False, "HWID_BANNED", "Это устройство заблокировано.")

    if lic.hwid and lic.hwid != hwid:
        if get_setting(db, "hwid_lock") == "1":
            log_event(
                db,
                "HWID_MISMATCH",
                key=lic.key,
                hwid=hwid,
                discord_id=lic.discord_id,
                ip=ip,
                detail=f"bound={lic.hwid[:8]}…",
            )
            return ActivationResult(
                False,
                "HWID_MISMATCH",
                "Ключ уже активирован на другом ПК. Запросите сброс HWID.",
            )
        lic.hwid = hwid
        lic.hwid_resets = (lic.hwid_resets or 0) + 1
        log_event(db, "HWID_REBOUND", key=lic.key, hwid=hwid, ip=ip)

    if not lic.hwid:
        lic.hwid = hwid

    lic.activated_at = lic.activated_at or utcnow()
    lic.last_seen_at = utcnow()
    lic.last_ip = ip
    db.commit()
    db.refresh(lic)
    log_event(db, "ACTIVATED", key=lic.key, hwid=hwid, discord_id=lic.discord_id, ip=ip)
    return ActivationResult(True, "OK", "Успешная активация.", lic)


def touch(db: Session, lic: License, ip: str | None = None) -> None:
    lic.last_seen_at = utcnow()
    if ip:
        lic.last_ip = ip
    db.commit()
