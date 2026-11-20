"""Подписи, токены и проверка админ-доступа.

Схема простая и намеренно без JWT-библиотек:
  * сервер подписывает КАЖДЫЙ ответ HMAC-SHA256(client_secret);
  * клиент проверяет подпись — подделанный/подменённый ответ не пройдёт;
  * клиент подписывает запросы (X-Timestamp + X-Signature);
  * токен сессии — это подписанный payload с key_id/hwid/exp.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Header, HTTPException, Request, status

from .config import settings


def canonical(data: Any) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign(data: Any, secret: str | None = None) -> str:
    return hmac.new(
        (secret or settings.client_secret).encode("utf-8"), canonical(data), hashlib.sha256
    ).hexdigest()


def signed(data: dict) -> dict:
    """Добавляет поле sig ко всему ответу клиенту."""
    body = dict(data)
    body["sig"] = sign(body)
    body["server_time"] = int(time.time())
    return body


# --------------------------------------------------------------------------
# Токен сессии
# --------------------------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def make_token(license_id: int, key: str, discord_id: int, hwid: str) -> tuple[str, int]:
    exp = int(time.time()) + settings.token_ttl_days * 86400
    payload = {
        "v": 1,
        "l": license_id,
        "k": key,
        "d": str(discord_id),
        "h": hwid_fingerprint(hwid),
        "e": exp,
        "n": secrets.token_hex(4),
    }
    encoded = _b64e(canonical(payload))
    return f"{encoded}.{sign(payload)}", exp


def parse_token(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)
        payload = json.loads(_b64d(encoded))
    except Exception:
        raise ValueError("bad token") from None
    if not hmac.compare_digest(sign(payload), signature):
        raise ValueError("bad signature")
    if int(payload.get("e", 0)) < int(time.time()):
        raise ValueError("expired")
    return payload


def hwid_fingerprint(hwid: str) -> str:
    return hmac.new(
        settings.client_secret.encode(), hwid.encode(), hashlib.sha256
    ).hexdigest()[:16]


# --------------------------------------------------------------------------
# Проверка подписи клиентского запроса
# --------------------------------------------------------------------------
def verify_client_request(ts: str | None, sig: str | None, body: bytes) -> None:
    if not settings.client_sign_required:
        return
    if not ts or not sig:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNSIGNED", "message": "Подпись запроса обязательна"},
        )
    try:
        timestamp = int(ts)
    except ValueError:
        raise HTTPException(401, {"code": "UNSIGNED", "message": "Плохой timestamp"})
    if abs(int(time.time()) - timestamp) > 300:
        raise HTTPException(401, {"code": "STALE", "message": "Устаревший запрос"})
    expected = sign({"ts": ts, "body": hashlib.sha256(body).hexdigest()})
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(403, {"code": "BAD_SIGNATURE", "message": "Неверная подпись"})


async def request_body(request: Request) -> bytes:
    return await request.body()


# --------------------------------------------------------------------------
# Админ-доступ
# --------------------------------------------------------------------------
def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=403, detail="admin key required")
