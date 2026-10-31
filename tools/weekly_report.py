#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Недельный отчёт по эффективности публикаций (ТЗ §10).

Пишет текст в docs/reports/ и, если задан ADMIN_CHAT_ID, отправляет админу.
Стратегию бот не меняет: только отчёт и рекомендации.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.analytics import render_report, weekly_report  # noqa: E402
from bot.config import config  # noqa: E402
from bot.db import PostedManager  # noqa: E402
from bot.logging_setup import setup_logging  # noqa: E402


def build_and_save() -> str:
    posted = PostedManager(config.db_file)
    try:
        rows = posted.stats_rows(config.report_days + 1)
    finally:
        posted.close()
    report = weekly_report(rows, days=config.report_days)
    text = render_report(report)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "docs", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"report-{datetime.now(timezone.utc):%Y-%m-%d}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"\nФайл: {path}")
    return text


async def send_to_admin(text: str) -> bool:
    if not config.admin_chat_id:
        return False
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.exceptions import TelegramAPIError

    bot = Bot(token=config.telegram_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(config.admin_chat_id, text)
        return True
    except TelegramAPIError as e:
        print(f"⚠️ Отчёт не отправлен: {e}")
        return False
    finally:
        await bot.session.close()


if __name__ == "__main__":
    setup_logging()
    report_text = build_and_save()
    if config.admin_chat_id:
        asyncio.run(send_to_admin(report_text))
