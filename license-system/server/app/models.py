"""Модели БД: лицензии, чёрный список HWID, события, настройки."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    """Наивный UTC — SQLite не хранит таймзоны, так проще сравнивать."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    hwid: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    hwid_resets: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    build_id: Mapped[str | None] = mapped_column(String(32), default=None)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "discord_id": str(self.discord_id),
            "hwid": self.hwid,
            "active": self.active,
            "created_at": iso(self.created_at),
            "activated_at": iso(self.activated_at),
            "last_seen_at": iso(self.last_seen_at),
            "hwid_resets": self.hwid_resets,
            "note": self.note,
            "build_id": self.build_id,
        }


class HwidBlacklist(Base):
    __tablename__ = "hwid_blacklist"

    hwid: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)

    def as_dict(self) -> dict:
        return {
            "hwid": self.hwid,
            "reason": self.reason,
            "created_at": iso(self.created_at),
            "created_by": self.created_by,
        }


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    discord_id: Mapped[int | None] = mapped_column(BigInteger, index=True, default=None)
    hwid: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    key: Mapped[str | None] = mapped_column(String(32), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "created_at": iso(self.created_at),
            "discord_id": str(self.discord_id) if self.discord_id else None,
            "hwid": self.hwid,
            "key": self.key,
            "detail": self.detail,
            "ip": self.ip,
        }


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=func.now()
    )


# Значения по умолчанию. Их можно менять через /admin/settings или команды бота.
DEFAULT_SETTINGS: dict[str, str] = {
    "app_enabled": "1",             # глобальный рубильник
    "click_delay_ms": "2000",       # задержка клика, мс
    "maintenance_message": "Технические работы. Попробуйте позже.",
    "hwid_lock": "1",               # 1 = ключ привязан к одному ПК
    "min_client_version": "1.0.0",
    "latest_version": "1.0.0",
}
