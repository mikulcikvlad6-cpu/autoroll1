"""Отправка ЛС через Discord REST API — без запущенного gateway.

Нужно, чтобы веб-процесс мог сам прислать ключ в личку после OAuth2.
"""
from __future__ import annotations

import httpx

from .config import settings

API = "https://discord.com/api/v10"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bot {settings.discord_bot_token}",
        "Content-Type": "application/json",
    }


def send_dm(user_id: int | str, content: str) -> bool:
    """True, если сообщение ушло. Если у пользователя закрыты ЛС — False."""
    if not settings.discord_bot_token:
        return False
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{API}/users/@me/channels",
                headers=_headers(),
                json={"recipient_id": str(user_id)},
            )
            if response.status_code not in (200, 201):
                return False
            channel_id = response.json()["id"]
            sent = client.post(
                f"{API}/channels/{channel_id}/messages",
                headers=_headers(),
                json={"content": content},
            )
            return sent.status_code in (200, 201)
    except Exception:
        return False


def fetch_user(access_token: str) -> dict | None:
    """Профиль пользователя по OAuth2 access_token."""
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                f"{API}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            return response.json() if response.status_code == 200 else None
    except Exception:
        return None


def exchange_code(code: str) -> dict | None:
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oauth_redirect_uri,
    }
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{API}/oauth2/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                return None
            return response.json()
    except Exception:
        return None
