#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Воркер автомодерации группы обсуждений канала «Доступ к интернету».

Запуск: `python moderationbot.py` (systemd-юнит deploy/post-bot-mod.service).
Работает long polling: токен постер-бота, группа @vlessvpnvmess, бот там админ
с правами удалять и ограничивать.

Правила безопасности:
- владельцу ничего не приходит само; лог — командой `/modlog` в личке бота;
- команды принимаются только от OWNER_ID;
- бот не отвечает в группе и не спорит: только удаление и короткая запись в лог;
- ошибки Bot API не роняют процесс: пауза и продолжение.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message

from bot.logging_setup import setup_logging
from bot.moderation import (
    ModerationStore,
    classify,
    render_log,
    render_stats,
)

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
CHAT_RAW = os.getenv("DISCUSSION_CHAT_ID", "").strip()
MOD_DB = os.getenv("MOD_DB", "/opt/post-bot-mod/moderation.db")
BAN_DAYS = int(os.getenv("MOD_BAN_DAYS", "7"))
FLOOD_COUNT = int(os.getenv("MOD_FLOOD_COUNT", "3"))
ADMIN_REFRESH_SEC = 600

store: ModerationStore
admin_ids: set[int] = set()
chat_id: int | str = CHAT_RAW


def _chat_target() -> int | str:
    raw = CHAT_RAW
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw if raw.startswith("@") else f"@{raw}"


def is_owner(user_id: int | None) -> bool:
    return bool(user_id) and user_id == OWNER_ID


def message_text(message: Message) -> str:
    parts = [message.text or "", message.caption or ""]
    return "\n".join(part for part in parts if part).strip()


async def refresh_admins(bot: Bot) -> None:
    """Кто в группе админ: их сообщения не трогаем ни при каких словах."""
    global admin_ids
    try:
        members = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError as exc:
        logger.warning("не удалось получить админов группы: %s", exc)
        return
    found = set()
    for member in members:
        if isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)):
            found.add(member.user.id)
    if found != admin_ids:
        logger.info("админы группы: %s", sorted(found))
    admin_ids = found


async def admin_refresher(bot: Bot) -> None:
    while True:
        await asyncio.sleep(ADMIN_REFRESH_SEC)
        await refresh_admins(bot)


async def punish(message: Message, verdict) -> None:
    """Удалить сообщение и, если нужно, ограничить автора."""
    action = verdict.action
    if not action:
        return
    try:
        await message.delete()
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await message.delete()
        except TelegramAPIError as err:
            logger.warning("повторное удаление не удалось: %s", err)
            return
    except TelegramAPIError as exc:
        logger.warning("удаление не удалось (message_id=%s): %s", message.message_id, exc)
        return

    user = message.from_user
    if action == "delete_ban" and user is not None:
        until = datetime.now(timezone.utc) + timedelta(days=BAN_DAYS)
        try:
            await message.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user.id,
                permissions={"can_send_messages": False},
                until_date=until,
            )
        except TelegramAPIError as exc:
            logger.warning("ограничение не применилось (user=%s): %s", user.id, exc)

    store.log(
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_id=user.id if user else None,
        username=(user.username if user else "") or "",
        action=action,
        reason=verdict.reason,
        text=message_text(message),
    )
    who = f"@{user.username}" if user and user.username else (user.id if user else "?")
    logger.info("🧹 %s → %s (%s) score=%s", who, action, verdict.reason, verdict.score)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def group_watch(message: Message) -> None:
        if message.sender_chat is not None:
            return  # пост из привязанного канала
        user = message.from_user
        if user is None or user.is_bot:
            return
        if user.id in admin_ids or is_owner(user.id):
            return

        text = message_text(message)
        repeats = store.seen_before(text, user.id) if text else 0
        verdict = classify(text,
                           has_media=bool(message.photo or message.video or message.document),
                           sender_is_new=repeats == 0)

        if not verdict.is_spam and repeats >= FLOOD_COUNT - 1:
            store.log(message.chat.id, message.message_id, user.id,
                      user.username or "", "delete_flood", f"повтор x{repeats + 1}", text)
            try:
                await message.delete()
                logger.info("🧹 флуд от %s: %s повторов", user.id, repeats + 1)
            except TelegramAPIError as exc:
                logger.warning("флуд не удалён: %s", exc)
            return

        await punish(message, verdict)

    @dp.message(F.chat.type == ChatType.PRIVATE, Command("modlog"))
    async def cmd_log(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        await message.answer(render_log(store.tail(10)))

    @dp.message(F.chat.type == ChatType.PRIVATE, Command("modstats"))
    async def cmd_stats(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        await message.answer(render_stats(store.stats(7)))

    @dp.message(F.chat.type == ChatType.PRIVATE, Command("modtest"))
    async def cmd_test(message: Message, command: CommandObject) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        sample = (command.args or "").strip()
        if not sample:
            await message.answer("Пришли так: /modtest <текст сообщения>")
            return
        verdict = classify(sample, sender_is_new=True)
        await message.answer(
            f"Режим проверки (ничего не удалено)\n"
            f"решение: {verdict.action or 'оставить'}\n"
            f"балл: {verdict.score}\n"
            f"причина: {verdict.reason}"
        )

    @dp.message(F.chat.type == ChatType.PRIVATE, Command("modhelp"))
    async def cmd_help(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        await message.answer(
            "🛡️ Автомодерация группы @vlessvpnvmess\n"
            "/modlog — последние 10 решений\n"
            "/modstats — сводка за 7 дней\n"
            "/modtest <текст> — проверить сообщение без удаления\n\n"
            "Режет: реклама, «заработок», продажа ключей и подписок, казино, "
            "займы, инвайты в чужие чаты, флуд одним текстом.\n"
            "Не трогает: вопросы, свои домены (dostupnety.website, rwr-2.top)."
        )

    return dp


async def main() -> int:
    global store, chat_id
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return 2
    if not OWNER_ID:
        logger.error("OWNER_ID не задан")
        return 2
    if not CHAT_RAW:
        logger.error("DISCUSSION_CHAT_ID не задан")
        return 2

    store = ModerationStore(MOD_DB)
    chat_id = _chat_target()

    bot = Bot(TOKEN)
    me = await bot.get_me()
    logger.info("бот @%s запущен, группа %s, лог %s", me.username, chat_id, MOD_DB)

    await refresh_admins(bot)
    asyncio.create_task(admin_refresher(bot))

    dp = build_dispatcher()
    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await bot.session.close()
        store.close()
    return 0


if __name__ == "__main__":
    setup_logging()
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
