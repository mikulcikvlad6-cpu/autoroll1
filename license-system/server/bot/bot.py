"""Discord-бот: выдача ключей, админ-команды, kill switch.

Запуск (из папки server):
    python bot/bot.py
Бот работает с той же БД, что и API (DATABASE_URL), поэтому отдельный сервер
для него не нужен — но нужен доступ к Postgres, если он удалённый.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import License  # noqa: E402
from app.services import (  # noqa: E402
    add_blacklist,
    app_enabled,
    find_by_discord,
    find_license,
    get_setting,
    get_settings_map,
    is_blacklisted,
    issue_license,
    log_event,
    remove_blacklist,
    reset_hwid,
    set_active,
    set_setting,
)

log = logging.getLogger("autoroll.bot")

intents = discord.Intents.default()
intents.members = True          # Server Members Intent
intents.message_content = True  # Message Content Intent (на всякий случай)

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# --------------------------------------------------------------------------
# Проверки доступа
# --------------------------------------------------------------------------
async def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id in settings.owner_ids:
        return True
    if isinstance(interaction.user, discord.Member):
        if settings.admin_role_id and any(
            role.id == settings.admin_role_id for role in interaction.user.roles
        ):
            return True
        if interaction.user.guild_permissions.administrator:
            return True
    return False


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_admin(interaction):
            return True
        raise app_commands.CheckFailure("Нет прав.")

    return app_commands.check(predicate)


def key_text(key: str) -> str:
    return f"`{key}`"


# --------------------------------------------------------------------------
# Пользовательские команды
# --------------------------------------------------------------------------
@bot.tree.command(name="getkey", description="Получить ключ доступа к AutoRoll")
async def getkey(interaction: discord.Interaction) -> None:
    with SessionLocal() as db:
        if not app_enabled(db):
            await interaction.response.send_message(
                "⛔ Сервис временно отключён администратором.", ephemeral=True
            )
            return
        lic, created = issue_license(db, interaction.user.id, note="discord-getkey")
        revoked = not lic.active
        key = lic.key

    if revoked:
        await interaction.response.send_message(
            "Ваша лицензия отозвана. Обратитесь к администратору.", ephemeral=True
        )
        return

    if settings.key_delivery == "direct":
        try:
            await interaction.user.send(
                f"**Ваш ключ AutoRoll:** {key_text(key)}\n"
                "Введите его при первом запуске `roll.exe`.\n"
                "Ключ привязан к вашему Discord-аккаунту."
            )
            await interaction.response.send_message(
                "✅ Ключ отправлен в личные сообщения.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не могу написать в ЛС — откройте личные сообщения для участников сервера "
                "и повторите команду.",
                ephemeral=True,
            )
        return

    url = f"{settings.public_base_url.rstrip('/')}/oauth/start"
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="Получить ключ через Discord",
            style=discord.ButtonStyle.link,
            url=url,
            emoji="🔑",
        )
    )
    text = (
        "Нажмите кнопку ниже и подтвердите вход — ключ появится на странице "
        "и придёт в личные сообщения."
        if created
        else "У вас уже есть лицензия. Кнопка ниже покажет ваш ключ."
    )
    await interaction.response.send_message(text, view=view, ephemeral=True)


@bot.tree.command(name="mykey", description="Отправить ваш ключ в личные сообщения")
async def mykey(interaction: discord.Interaction) -> None:
    with SessionLocal() as db:
        lic = find_by_discord(db, interaction.user.id)
        if lic is None:
            await interaction.response.send_message(
                "У вас пока нет ключа. Используйте `/getkey`.", ephemeral=True
            )
            return
        key, active, hwid = lic.key, lic.active, lic.hwid

    if not active:
        await interaction.response.send_message(
            "Лицензия отозвана. Обратитесь к администратору.", ephemeral=True
        )
        return
    binding = "привязан к ПК" if hwid else "ещё не активирован"
    try:
        await interaction.user.send(
            f"**Ваш ключ:** {key_text(key)}\nСтатус: {binding}."
        )
        await interaction.response.send_message("✅ Отправил в ЛС.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Не могу написать в ЛС — откройте личные сообщения.", ephemeral=True
        )


@bot.tree.command(name="status", description="Статус сервиса")
async def status_cmd(interaction: discord.Interaction) -> None:
    with SessionLocal() as db:
        enabled = app_enabled(db)
        values = get_settings_map(db)
    await interaction.response.send_message(
        f"Сервис: {'🟢 работает' if enabled else '🔴 отключён'}\n"
        f"Задержка клика: `{values.get('click_delay_ms')}` мс · "
        f"Версия: `{values.get('latest_version')}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="reset_hwid",
    description="Сбросить привязку ключа к ПК (доступно, если разрешено)",
)
async def self_reset_hwid(interaction: discord.Interaction) -> None:
    if settings.self_hwid_reset_days <= 0:
        await interaction.response.send_message(
            "Самосброс HWID отключён. Напишите администратору.", ephemeral=True
        )
        return
    with SessionLocal() as db:
        lic = find_by_discord(db, interaction.user.id)
        if lic is None:
            await interaction.response.send_message("У вас нет ключа.", ephemeral=True)
            return
        if not lic.hwid:
            await interaction.response.send_message(
                "Ключ ещё не привязан — сброс не нужен.", ephemeral=True
            )
            return
        reset_hwid(db, lic, by=f"self:{interaction.user.id}")
    await interaction.response.send_message(
        "✅ Привязка сброшена. Ключ привяжется к ПК при следующем запуске.",
        ephemeral=True,
    )


# --------------------------------------------------------------------------
# Админ-команды
# --------------------------------------------------------------------------
admin_group = app_commands.Group(name="admin", description="Управление лицензиями")


@admin_group.command(name="issue", description="Выдать ключ пользователю")
@admin_only()
async def admin_issue(
    interaction: discord.Interaction,
    user: discord.User,
    note: str = "",
) -> None:
    with SessionLocal() as db:
        lic, created = issue_license(db, user.id, note=note or "manual")
    verb = "создан" if created else "уже существует"
    await interaction.response.send_message(
        f"Ключ для {user.mention} {verb}: {key_text(lic.key)}", ephemeral=True
    )


@admin_group.command(name="info", description="Информация о ключе")
@admin_only()
async def admin_info(interaction: discord.Interaction, key: str) -> None:
    with SessionLocal() as db:
        lic = find_license(db, key)
        if lic is None:
            await interaction.response.send_message("Ключ не найден.", ephemeral=True)
            return
        data = lic.as_dict()
    await interaction.response.send_message(
        f"**Ключ** `{data['key']}`\n"
        f"Discord: <@{data['discord_id']}> (`{data['discord_id']}`)\n"
        f"HWID: `{data['hwid'] or '—'}`\n"
        f"Активен: `{data['active']}` · сбросов HWID: `{data['hwid_resets']}`\n"
        f"Последний запуск: `{data['last_seen_at'] or '—'}`",
        ephemeral=True,
    )


@admin_group.command(name="revoke", description="Отозвать ключ")
@admin_only()
async def admin_revoke(interaction: discord.Interaction, key: str) -> None:
    with SessionLocal() as db:
        lic = find_license(db, key)
        if lic is None:
            await interaction.response.send_message("Ключ не найден.", ephemeral=True)
            return
        set_active(db, lic, False)
        user_id = lic.discord_id
    try:
        user = await bot.fetch_user(user_id)
        await user.send("⛔ Ваша лицензия AutoRoll отозвана.")
    except Exception:
        pass
    await interaction.response.send_message(f"Ключ `{key.upper()}` отозван.", ephemeral=True)


@admin_group.command(name="unrevoke", description="Вернуть ключ в работу")
@admin_only()
async def admin_unrevoke(interaction: discord.Interaction, key: str) -> None:
    with SessionLocal() as db:
        lic = find_license(db, key)
        if lic is None:
            await interaction.response.send_message("Ключ не найден.", ephemeral=True)
            return
        set_active(db, lic, True)
    await interaction.response.send_message(f"Ключ `{key.upper()}` снова активен.", ephemeral=True)


@admin_group.command(name="reset_hwid", description="Сбросить привязку ключа к ПК")
@admin_only()
async def admin_reset_hwid(interaction: discord.Interaction, key: str) -> None:
    with SessionLocal() as db:
        lic = find_license(db, key)
        if lic is None:
            await interaction.response.send_message("Ключ не найден.", ephemeral=True)
            return
        reset_hwid(db, lic, by=f"admin:{interaction.user.id}")
    await interaction.response.send_message("✅ HWID сброшен.", ephemeral=True)


@admin_group.command(name="ban", description="Забанить HWID")
@admin_only()
async def admin_ban(interaction: discord.Interaction, hwid: str, reason: str = "") -> None:
    with SessionLocal() as db:
        add_blacklist(db, hwid, reason or "manual", str(interaction.user.id))
    await interaction.response.send_message(
        f"🚫 HWID `{hwid.upper()}` в чёрном списке.", ephemeral=True
    )


@admin_group.command(name="unban", description="Убрать HWID из чёрного списка")
@admin_only()
async def admin_unban(interaction: discord.Interaction, hwid: str) -> None:
    with SessionLocal() as db:
        ok = remove_blacklist(db, hwid)
    await interaction.response.send_message(
        "✅ Удалён из чёрного списка." if ok else "Такого HWID нет.", ephemeral=True
    )


@admin_group.command(name="hwid", description="Проверить HWID по чёрному списку")
@admin_only()
async def admin_hwid(interaction: discord.Interaction, hwid: str) -> None:
    with SessionLocal() as db:
        row = is_blacklisted(db, hwid)
    if row:
        await interaction.response.send_message(
            f"🚫 В бане: `{row.reason or '—'}`", ephemeral=True
        )
    else:
        await interaction.response.send_message("✅ HWID чист.", ephemeral=True)


@admin_group.command(name="killswitch", description="Глобально включить/выключить клиент")
@admin_only()
async def admin_killswitch(interaction: discord.Interaction, enabled: bool) -> None:
    with SessionLocal() as db:
        set_setting(db, "app_enabled", "1" if enabled else "0")
        db.commit()
        log_event(
            db,
            "KILLSWITCH",
            discord_id=interaction.user.id,
            detail=f"app_enabled={enabled} via bot",
        )
    if enabled:
        await interaction.response.send_message(
            "🟢 Клиент включён. Все запущенные копии продолжат работу.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "🔴 KILL SWITCH. В течение 10–15 секунд все клиенты получат команду "
            "на остановку и самоудаление.",
            ephemeral=True,
        )


@admin_group.command(name="delay", description="Задержка клика в миллисекундах")
@admin_only()
async def admin_delay(interaction: discord.Interaction, ms: app_commands.Range[int, 100, 60000]) -> None:
    with SessionLocal() as db:
        set_setting(db, "click_delay_ms", str(ms))
    await interaction.response.send_message(f"Задержка: `{ms}` мс.", ephemeral=True)


@admin_group.command(name="stats", description="Сводка по лицензиям")
@admin_only()
async def admin_stats(interaction: discord.Interaction) -> None:
    from sqlalchemy import func, select

    from app.models import Event, HwidBlacklist

    with SessionLocal() as db:
        total = db.scalar(select(func.count(License.id))) or 0
        active = db.scalar(select(func.count(License.id)).where(License.active.is_(True))) or 0
        bound = db.scalar(select(func.count(License.id)).where(License.hwid.is_not(None))) or 0
        banned = db.scalar(select(func.count(HwidBlacklist.hwid))) or 0
        tamper = db.scalar(select(func.count(Event.id)).where(Event.kind == "TAMPER")) or 0
        enabled = app_enabled(db)
    await interaction.response.send_message(
        f"**Ключей:** {total} · активных {active} · привязано {bound}\n"
        f"**В бане HWID:** {banned} · **сигналов о взломе:** {tamper}\n"
        f"**Клиент:** {'🟢 on' if enabled else '🔴 off'}",
        ephemeral=True,
    )


@admin_group.command(name="events", description="Последние события")
@admin_only()
async def admin_events(interaction: discord.Interaction, limit: int = 10) -> None:
    from sqlalchemy import select

    from app.models import Event

    with SessionLocal() as db:
        rows = db.scalars(
            select(Event).order_by(Event.created_at.desc()).limit(max(1, min(limit, 25)))
        ).all()
    if not rows:
        await interaction.response.send_message("Событий нет.", ephemeral=True)
        return
    lines = [
        f"`{e.created_at:%d.%m %H:%M}` **{e.kind}** "
        f"{('`' + e.key + '`') if e.key else ''} {('`' + (e.hwid or '')[:12] + '…`') if e.hwid else ''} "
        f"{(e.detail or '')[:80]}"
        for e in rows
    ]
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


bot.tree.add_command(admin_group)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.CheckFailure):
        message = "⛔ Недостаточно прав."
    else:
        log.exception("command error", exc_info=error)
        message = "Ошибка выполнения команды. Подробности в логах."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_ready() -> None:
    log.info("Бот запущен как %s (%s)", bot.user, bot.user.id if bot.user else "?")
    try:
        if settings.guild_id:
            guild = discord.Object(id=settings.guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        log.info("Синхронизировано команд: %s", len(synced))
    except Exception:
        log.exception("Не удалось синхронизировать команды")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not settings.discord_bot_token:
        raise SystemExit("DISCORD_BOT_TOKEN не задан в .env")
    init_db()
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
