"""Админ-API. Доступ по заголовку X-Admin-Key (тот же ключ использует бот)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Event, HwidBlacklist, License
from ..schemas import BlacklistIn, IssueIn, KillSwitchIn, RevokeIn, SettingsIn
from ..security import require_admin
from ..services import (
    add_blacklist,
    app_enabled,
    find_license,
    get_settings_map,
    issue_license,
    normalize_key,
    remove_blacklist,
    reset_hwid,
    set_active,
    set_setting,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _license_or_404(db: Session, key: str) -> License:
    lic = find_license(db, key)
    if lic is None:
        raise HTTPException(404, "license not found")
    return lic


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    total = db.scalar(select(func.count(License.id))) or 0
    active = db.scalar(select(func.count(License.id)).where(License.active.is_(True))) or 0
    bound = db.scalar(select(func.count(License.id)).where(License.hwid.is_not(None))) or 0
    banned = db.scalar(select(func.count(HwidBlacklist.hwid))) or 0
    tamper = db.scalar(select(func.count(Event.id)).where(Event.kind == "TAMPER")) or 0
    return {
        "total_keys": total,
        "active_keys": active,
        "bound_keys": bound,
        "blacklisted_hwids": banned,
        "tamper_events": tamper,
        "app_enabled": app_enabled(db),
        "settings": get_settings_map(db),
    }


@router.get("/licenses")
def list_licenses(
    limit: int = 100, offset: int = 0, db: Session = Depends(get_db)
) -> dict:
    limit = max(1, min(limit, 500))
    rows = db.scalars(
        select(License).order_by(License.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"items": [r.as_dict() for r in rows], "limit": limit, "offset": offset}


@router.get("/licenses/{key}")
def get_license(key: str, db: Session = Depends(get_db)) -> dict:
    return _license_or_404(db, key).as_dict()


@router.post("/licenses")
def create_license(payload: IssueIn, db: Session = Depends(get_db)) -> dict:
    if not payload.discord_id.strip().isdigit():
        raise HTTPException(400, "discord_id must be numeric")
    lic, created = issue_license(
        db, int(payload.discord_id), payload.note, payload.hwid
    )
    return {"created": created, "license": lic.as_dict()}


@router.post("/licenses/{key}/active")
def set_license_active(key: str, payload: RevokeIn, db: Session = Depends(get_db)) -> dict:
    lic = _license_or_404(db, key)
    return set_active(db, lic, payload.active).as_dict()


@router.post("/licenses/{key}/reset-hwid")
def reset_license_hwid(key: str, db: Session = Depends(get_db)) -> dict:
    return reset_hwid(db, _license_or_404(db, key)).as_dict()


@router.delete("/licenses/{key}")
def delete_license(key: str, db: Session = Depends(get_db)) -> dict:
    lic = _license_or_404(db, key)
    db.delete(lic)
    db.commit()
    return {"ok": True, "deleted": normalize_key(key)}


@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(HwidBlacklist).order_by(HwidBlacklist.created_at.desc())).all()
    return {"items": [r.as_dict() for r in rows]}


@router.post("/blacklist")
def create_blacklist(payload: BlacklistIn, db: Session = Depends(get_db)) -> dict:
    return add_blacklist(db, payload.hwid, payload.reason, "admin").as_dict()


@router.delete("/blacklist/{hwid}")
def delete_blacklist(hwid: str, db: Session = Depends(get_db)) -> dict:
    if not remove_blacklist(db, hwid):
        raise HTTPException(404, "hwid not found")
    return {"ok": True, "hwid": hwid.upper()}


@router.get("/events")
def list_events(kind: str | None = None, limit: int = 50, db: Session = Depends(get_db)) -> dict:
    limit = max(1, min(limit, 200))
    query = select(Event).order_by(Event.created_at.desc()).limit(limit)
    if kind:
        query = query.where(Event.kind == kind)
    return {"items": [e.as_dict() for e in db.scalars(query).all()]}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return get_settings_map(db)


@router.post("/settings")
def update_settings(payload: SettingsIn, db: Session = Depends(get_db)) -> dict:
    if payload.app_enabled is not None:
        set_setting(db, "app_enabled", "1" if payload.app_enabled else "0")
        db.add(Event(kind="KILLSWITCH", detail=f"app_enabled={payload.app_enabled}"))
        db.commit()
    if payload.click_delay_ms is not None:
        set_setting(db, "click_delay_ms", str(payload.click_delay_ms))
    if payload.maintenance_message is not None:
        set_setting(db, "maintenance_message", payload.maintenance_message)
    if payload.hwid_lock is not None:
        set_setting(db, "hwid_lock", "1" if payload.hwid_lock else "0")
    if payload.latest_version is not None:
        set_setting(db, "latest_version", payload.latest_version)
    return get_settings_map(db)


@router.post("/killswitch")
def killswitch(payload: KillSwitchIn, db: Session = Depends(get_db)) -> dict:
    set_setting(db, "app_enabled", "1" if payload.enabled else "0")
    db.add(
        Event(kind="KILLSWITCH", detail=f"app_enabled={payload.enabled} via admin API")
    )
    db.commit()
    return {"ok": True, "app_enabled": payload.enabled}
