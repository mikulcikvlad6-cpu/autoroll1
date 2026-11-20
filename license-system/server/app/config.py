"""Конфигурация сервера. Все значения читаются из переменных окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- база данных ---------------------------------------------------
    # Локально: sqlite:///./data/roll.db
    # Render/Railway: postgresql+psycopg://user:pass@host/db  (см. DATABASE_URL)
    database_url: str = _env("DATABASE_URL", "sqlite:///./data/roll.db")

    # --- секреты -------------------------------------------------------
    # Общий секрет сервера и клиента (roll.exe). Никогда не публикуй.
    client_secret: str = _env("CLIENT_SECRET", "dev-client-secret-change-me")
    # Ключ для админ-API (бот и админ-панель).
    admin_api_key: str = _env("ADMIN_API_KEY", "dev-admin-key-change-me")

    # --- Discord -------------------------------------------------------
    discord_client_id: str = _env("DISCORD_CLIENT_ID")
    discord_client_secret: str = _env("DISCORD_CLIENT_SECRET")
    discord_bot_token: str = _env("DISCORD_BOT_TOKEN")
    oauth_redirect_uri: str = _env(
        "DISCORD_REDIRECT_URI", "http://127.0.0.1:8000/oauth/callback"
    )
    # Публичный адрес сервера (для OAuth-ссылок и кнопок).
    public_base_url: str = _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

    # --- бизнес-логика -------------------------------------------------
    key_prefix: str = _env("KEY_PREFIX", "LARP")
    token_ttl_days: int = _env_int("TOKEN_TTL_DAYS", 7)
    # Сколько часов клиент может работать без связи с сервером.
    offline_grace_hours: int = _env_int("OFFLINE_GRACE_HOURS", 24)
    # Требовать подпись клиентских запросов (в проде включить).
    client_sign_required: bool = _env("CLIENT_SIGN_REQUIRED", "0") in ("1", "true", "yes")
    # Автоматически банить HWID при сигнале о взломе.
    auto_ban_on_tamper: bool = _env("AUTO_BAN_ON_TAMPER", "0") in ("1", "true", "yes")
    # Цена одного HWID-сброса в сутках кулдауна (0 = только админ).
    self_hwid_reset_days: int = _env_int("SELF_HWID_RESET_DAYS", 0)

    # --- доступ --------------------------------------------------------
    owner_ids: frozenset[int] = field(
        default_factory=lambda: frozenset(
            int(x) for x in _env("OWNER_IDS").replace(" ", "").split(",") if x.isdigit()
        )
    )
    admin_role_id: int = _env_int("ADMIN_ROLE_ID", 0)
    guild_id: int = _env_int("DISCORD_GUILD_ID", 0)

    # Ключ доставки: oauth (через OAuth2-страницу) или direct (сразу в ЛС).
    key_delivery: str = _env("KEY_DELIVERY", "oauth").lower()

    # CORS для админ-панели (если появится веб-морда).
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip() for x in _env("CORS_ORIGINS", "").split(",") if x.strip()
        )
    )


settings = Settings()

# Каталог для sqlite-файла и прочих данных.
DATA_DIR = BASE_DIR / "data"
if settings.database_url.startswith("sqlite"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
