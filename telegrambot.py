#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа бота. Вся логика — в пакете bot/.

Запуск не изменился: `python telegrambot.py` (так его вызывает GitHub Actions).
"""
import asyncio
import sys

from bot.logging_setup import setup_logging
from bot.pipeline import main

if __name__ == "__main__":
    setup_logging()
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("🛑 Прервано пользователем")
        sys.exit(130)
