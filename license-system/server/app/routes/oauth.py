"""Discord OAuth2: /oauth/start -> Discord -> /oauth/callback -> ключ в ЛС и на странице."""
from __future__ import annotations

import html
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..discord_api import exchange_code, fetch_user, send_dm
from ..security import _b64d, _b64e, sign
from ..services import issue_license, log_event

router = APIRouter(tags=["oauth"])

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"


def make_state() -> str:
    payload = {"ts": int(time.time()), "r": secrets.token_hex(8)}
    return f"{_b64e(json.dumps(payload).encode())}.{sign(payload)}"


def check_state(state: str) -> bool:
    try:
        encoded, signature = state.split(".", 1)
        payload = json.loads(_b64d(encoded))
    except Exception:
        return False
    if sign(payload) != signature:
        return False
    return int(payload.get("ts", 0)) + 600 > int(time.time())


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoRoll — лицензия</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f14;
color:#e6edf3;font:16px/1.5 system-ui,Segoe UI,Roboto,sans-serif}}
.card{{width:min(560px,92vw);background:#121821;border:1px solid #22303f;border-radius:18px;
padding:32px;box-shadow:0 20px 60px rgba(0,0,0,.5)}}
h1{{font-size:22px;margin:0 0 6px}} p{{color:#9fb0c3;margin:8px 0}}
.key{{font:600 24px/1.4 ui-monospace,Consolas,monospace;letter-spacing:2px;color:#6ee7b7;
background:#0b1118;border:1px dashed #2b3d50;border-radius:12px;padding:16px;text-align:center;
margin:18px 0;user-select:all}}
button{{background:#10b981;color:#052e1f;border:0;border-radius:10px;padding:12px 18px;
font-weight:700;cursor:pointer}} button:active{{transform:translateY(1px)}}
.note{{font-size:13px;color:#7d8fa3;border-top:1px solid #22303f;margin-top:22px;padding-top:14px}}
.err{{color:#f87171}}
</style></head><body><div class="card">
<h1>{title}</h1>
<p>{subtitle}</p>
{body}
<div class="note">Ключ привязан к вашему Discord-аккаунту. Первый запуск привяжет его к ПК.
Сменить ПК можно только через сброс HWID.</div>
</div></body></html>"""


@router.get("/oauth/start", response_model=None)
def oauth_start():
    if not settings.discord_client_id or not settings.discord_client_secret:
        return HTMLResponse(
            PAGE.format(
                title="OAuth2 не настроен",
                subtitle="Администратор не указал DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET.",
                body="",
            ),
            status_code=503,
        )
    params = {
        "client_id": settings.discord_client_id,
        "response_type": "code",
        "scope": "identify",
        "redirect_uri": settings.oauth_redirect_uri,
        "state": make_state(),
        "prompt": "consent",
    }
    return RedirectResponse(f"{AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@router.get("/oauth/callback")
def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if error:
        return HTMLResponse(
            PAGE.format(
                title="Доступ отклонён",
                subtitle="Вы отменили авторизацию. Попробуйте снова.",
                body="",
            ),
            status_code=400,
        )
    if not code or not state or not check_state(state):
        return HTMLResponse(
            PAGE.format(
                title="Ссылка устарела",
                subtitle="Откройте новую ссылку командой /getkey в Discord.",
                body="",
            ),
            status_code=400,
        )

    token_data = exchange_code(code)
    if not token_data:
        return HTMLResponse(
            PAGE.format(
                title="Ошибка Discord",
                subtitle="Не удалось обменять код на токен. Попробуйте ещё раз.",
                body="",
            ),
            status_code=502,
        )

    user = fetch_user(token_data.get("access_token", ""))
    if not user or "id" not in user:
        return HTMLResponse(
            PAGE.format(
                title="Ошибка профиля",
                subtitle="Не удалось получить ваш Discord ID.",
                body="",
            ),
            status_code=502,
        )

    discord_id = int(user["id"])
    lic, created = issue_license(db, discord_id, note="oauth2")
    log_event(db, "OAUTH_LOGIN", discord_id=discord_id, key=lic.key)
    if not lic.active:
        return HTMLResponse(
            PAGE.format(
                title="Доступ закрыт",
                subtitle="Ваша лицензия отозвана администратором.",
                body="",
            ),
            status_code=403,
        )

    username = html.escape(user.get("username", "user"))
    dm_text = (
        f"**Ваш ключ AutoRoll:** `{lic.key}`\n"
        f"Скачайте `roll.exe` и введите ключ при первом запуске.\n"
        f"Ключ привязан к аккаунту {username} ({discord_id})."
    )
    dm_sent = send_dm(discord_id, dm_text)

    body = (
        f'<div class="key">{html.escape(lic.key)}</div>'
        f'<button onclick="navigator.clipboard.writeText(\'{lic.key}\');this.textContent=\'Скопировано\'">'
        f"Скопировать ключ</button>"
        f'<p style="margin-top:18px">'
        + ("Ключ также отправлен вам в личные сообщения." if dm_sent else
           "Не удалось отправить ЛС — проверьте, что ЛС от участников сервера открыты.")
        + "</p>"
    )
    title = "Ключ готов" if created else "Ваш ключ"
    return HTMLResponse(
        PAGE.format(
            title=title,
            subtitle=f"Аккаунт: {username} · ID {discord_id}",
            body=body,
        )
    )
