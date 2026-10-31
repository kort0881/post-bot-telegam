#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Процесс команд админа: очередь, статистика, ручная публикация.

Запуск: `python adminbot.py` (нужны TELEGRAM_BOT_TOKEN, ADMIN_IDS и общая с
ботом база DB_FILE). Публикацию по расписанию этот процесс не запускает.
"""
import asyncio
import sys

from bot.admin import main
from bot.logging_setup import setup_logging

if __name__ == "__main__":
    setup_logging()
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("🛑 Прервано пользователем")
        sys.exit(130)
