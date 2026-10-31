#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сбор метрик опубликованных постов: окна 1ч/6ч/24ч (ТЗ §10).

Запускается по расписанию. Просмотры берутся с публичной страницы поста;
недоступная метрика остаётся пустой, а не нулевой.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.analytics import due_windows, fetch_message_metrics  # noqa: E402
from bot.config import config  # noqa: E402
from bot.db import PostedManager  # noqa: E402
from bot.logging_setup import setup_logging  # noqa: E402

logger = logging.getLogger(__name__)


def channel_username() -> str:
    if config.channel_username:
        return config.channel_username
    cid = (config.channel_id or "").strip()
    return cid.lstrip("@") if cid.startswith("@") else ""


async def run(limit: int = 60) -> dict:
    username = channel_username()
    if not username:
        logger.warning("⚠️ CHANNEL_USERNAME не задан, метрики не собрать")
        return {"posts": 0, "windows": 0, "failed": 0, "reason": "no_channel"}

    posted = PostedManager(config.db_file)
    result = {"posts": 0, "windows": 0, "failed": 0}
    cache: dict = {}
    try:
        for row in posted.stats_pending_metrics(limit):
            windows = due_windows(row)
            if not windows:
                continue
            message_id = row["message_id"]
            if message_id not in cache:
                cache[message_id] = await fetch_message_metrics(username, message_id)
            metrics = cache[message_id]
            views = metrics.get("views")
            if views is None:
                result["failed"] += 1
                logger.info(f"  ⚠️ просмотры недоступны: #{message_id}")
                continue
            for column in windows:
                posted.save_window_metric(message_id, column, views, "t.me")
                result["windows"] += 1
            if "views_24h" in windows:
                posted.save_engagement_metrics(
                    message_id,
                    forwards=metrics.get("forwards"),
                    reactions=metrics.get("reactions"),
                    comments=metrics.get("comments"),
                )
            result["posts"] += 1
    finally:
        posted.close()
    logger.info(f"📈 Метрики: {result}")
    return result


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run())
