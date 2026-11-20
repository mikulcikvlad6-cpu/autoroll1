"""Pydantic-схемы запросов/ответов."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ActivateIn(BaseModel):
    key: str = Field(min_length=4, max_length=40)
    hwid: str = Field(min_length=8, max_length=64)
    discord_id: str | None = None
    client_version: str = "1.0.0"


class VerifyIn(BaseModel):
    token: str
    hwid: str
    client_version: str = "1.0.0"


class TamperIn(BaseModel):
    hwid: str
    kind: str = "unknown"
    detail: str | None = None
    token: str | None = None
    discord_id: str | None = None


class IssueIn(BaseModel):
    discord_id: str
    note: str | None = None
    hwid: str | None = None


class RevokeIn(BaseModel):
    active: bool = True


class BlacklistIn(BaseModel):
    hwid: str
    reason: str | None = None


class SettingsIn(BaseModel):
    app_enabled: bool | None = None
    click_delay_ms: int | None = Field(default=None, ge=100, le=60000)
    maintenance_message: str | None = None
    hwid_lock: bool | None = None
    latest_version: str | None = None


class KillSwitchIn(BaseModel):
    enabled: bool
