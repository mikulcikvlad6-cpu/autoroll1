"""Публичное API для roll.exe: активация, проверка, kill switch, сигналы о взломе."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import utcnow
from ..ratelimit import client_ip, limiter
from ..schemas import ActivateIn, TamperIn, VerifyIn
from ..security import (
    hwid_fingerprint,
    make_token,
    parse_token,
    sign,
    signed,
    verify_client_request,
)
from ..services import (
    activate,
    app_enabled,
    find_license,
    get_setting,
    is_blacklisted,
    log_event,
    touch,
)
from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["client"])


@router.get("/health")
def health() -> dict:
    return {"ok": True, "service": "autoroll-license", "time": utcnow().isoformat()}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    """Публичный статус без секретов — удобно для мониторинга."""
    return signed(
        {
            "ok": True,
            "enabled": app_enabled(db),
            "message": "" if app_enabled(db) else get_setting(db, "maintenance_message"),
            "latest_version": get_setting(db, "latest_version"),
        }
    )


async def _signed_body(request: Request) -> bytes:
    body = await request.body()
    verify_client_request(
        request.headers.get("x-timestamp"), request.headers.get("x-signature"), body
    )
    if not limiter.hit(client_ip(request), limit=30, window_seconds=60):
        raise HTTPException(429, {"code": "RATE_LIMIT", "message": "Слишком часто"})
    return body


@router.post("/activate")
async def activate_endpoint(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await _signed_body(request)
    try:
        payload = ActivateIn.model_validate_json(body)
    except Exception:
        raise HTTPException(422, {"code": "BAD_REQUEST", "message": "Некорректные данные"})

    ip = client_ip(request)
    discord_id = int(payload.discord_id) if payload.discord_id and payload.discord_id.isdigit() else None
    result = activate(db, payload.key, payload.hwid, discord_id, ip)

    if not result.ok or result.lic is None:
        status_code = {
            "HWID_BANNED": 403,
            "KEY_REVOKED": 403,
            "KEY_NOT_FOUND": 404,
            "HWID_MISMATCH": 409,
            "WRONG_ACCOUNT": 403,
            "BAD_HWID": 400,
        }.get(result.code, 400)
        raise HTTPException(status_code, {"code": result.code, "message": result.message})

    lic = result.lic
    token, exp = make_token(lic.id, lic.key, lic.discord_id, lic.hwid or payload.hwid)
    return signed(
        {
            "ok": True,
            "code": "OK",
            "token": token,
            "expires_at": exp,
            "click_delay_ms": int(get_setting(db, "click_delay_ms") or 2000),
            "enabled": app_enabled(db),
            "license": {
                "key": lic.key,
                "discord_id": str(lic.discord_id),
                "hwid": lic.hwid,
            },
        }
    )


@router.post("/verify")
async def verify_endpoint(request: Request, db: Session = Depends(get_db)) -> dict:
    """Вызывается при старте и каждые 10–15 секунд из фонового потока."""
    body = await _signed_body(request)
    try:
        payload = VerifyIn.model_validate_json(body)
    except Exception:
        raise HTTPException(422, {"code": "BAD_REQUEST", "message": "Некорректные данные"})

    try:
        token_data = parse_token(payload.token)
    except ValueError:
        raise HTTPException(401, {"code": "BAD_TOKEN", "message": "Сессия недействительна"})

    lic = find_license(db, token_data.get("k", ""))
    if lic is None or lic.id != token_data.get("l"):
        raise HTTPException(401, {"code": "BAD_TOKEN", "message": "Лицензия не найдена"})
    if token_data.get("h") != hwid_fingerprint(payload.hwid):
        log_event(
            db,
            "TOKEN_HWID_MISMATCH",
            key=lic.key,
            hwid=payload.hwid,
            discord_id=lic.discord_id,
            ip=client_ip(request),
        )
        raise HTTPException(401, {"code": "TOKEN_HWID_MISMATCH", "message": "Токен не от этого ПК"})

    if is_blacklisted(db, payload.hwid) or is_blacklisted(db, lic.hwid):
        log_event(db, "BLOCKED_BLACKLIST", key=lic.key, hwid=payload.hwid, ip=client_ip(request))
        return signed(
            {
                "ok": True,
                "enabled": False,
                "code": "HWID_BANNED",
                "message": "Устройство заблокировано.",
                "kill": True,
            }
        )

    if not lic.active:
        return signed(
            {
                "ok": True,
                "enabled": False,
                "code": "KEY_REVOKED",
                "message": "Лицензия отозвана.",
                "kill": False,
            }
        )

    if not app_enabled(db):
        return signed(
            {
                "ok": True,
                "enabled": False,
                "code": "DISABLED_BY_ADMIN",
                "message": get_setting(db, "maintenance_message"),
                "kill": True,
            }
        )

    touch(db, lic, client_ip(request))
    return signed(
        {
            "ok": True,
            "enabled": True,
            "code": "OK",
            "message": "",
            "click_delay_ms": int(get_setting(db, "click_delay_ms") or 2000),
            "latest_version": get_setting(db, "latest_version"),
        }
    )


@router.post("/tamper")
async def tamper_endpoint(request: Request, db: Session = Depends(get_db)) -> dict:
    """Сигнал «кто-то ковыряет клиент». Пишется в события, виден в /admin/events."""
    body = await _signed_body(request)
    try:
        payload = TamperIn.model_validate_json(body)
    except Exception:
        raise HTTPException(422, {"code": "BAD_REQUEST", "message": "Некорректные данные"})

    ip = client_ip(request)
    discord_id = int(payload.discord_id) if payload.discord_id and payload.discord_id.isdigit() else None
    detail = (payload.detail or "")[:500]
    log_event(
        db,
        "TAMPER",
        hwid=payload.hwid,
        discord_id=discord_id,
        detail=f"{payload.kind}: {detail}",
        ip=ip,
    )

    banned = False
    if settings.auto_ban_on_tamper:
        from ..services import add_blacklist

        add_blacklist(db, payload.hwid, f"auto-ban: tamper/{payload.kind}", "system")
        banned = True

    return signed(
        {
            "ok": True,
            "blacklisted": banned,
            "message": "Сигнал принят.",
            "click_delay_ms": 5000 if not banned else 0,
        }
    )
