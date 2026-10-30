#!/usr/bin/env python3
"""Сухой прогон без публикации: RSS → фильтры → рейтинг → топ.

Ничего не отправляет в канал и не вызывает LLM. Нужен, чтобы видеть, что именно
проходит фильтры и как считается рейтинг.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import PostedManager  # noqa: E402
from bot.feeds import load_all_feeds  # noqa: E402
from bot.logging_setup import setup_logging  # noqa: E402
from bot.pipeline import filter_and_dedupe, select_candidates  # noqa: E402


async def main() -> int:
    setup_logging()
    posted = PostedManager()
    try:
        raw = await load_all_feeds()
        print(f"\n=== Сырых материалов: {len(raw)}")
        candidates = filter_and_dedupe(raw, posted)
        print(f"=== Прошли фильтры: {len(candidates)}")
        ranked = select_candidates(candidates, posted)
        print("\n=== ТОП-15 ===")
        for i, item in enumerate(ranked[:15], start=1):
            print(f"{i:2}. [{item.effective:3}] [{item.article.source}] {item.article.title[:80]}")
            print(f"    {item.score.as_dict()}")
            if item.penalty:
                print(f"    штрафы: {item.penalty} ({', '.join(item.penalty_reasons)})")
    finally:
        posted.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
