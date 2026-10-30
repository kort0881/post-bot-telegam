#!/usr/bin/env python3
"""Демонстрационный прогон: по одному посту каждого из пяти форматов.

Ничего не публикует в канал: только собирает примеры на реальных RSS-материалах
и складывает их в docs/examples/. Нужен для проверки редакционной системы
на живых данных (ТЗ §16, п.7) и для ручного просмотра владельцем.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.assessment import assess_article  # noqa: E402
from bot.config import config  # noqa: E402
from bot.db import PostedManager  # noqa: E402
from bot.editorial import build_post_text, generate_post  # noqa: E402
from bot.extract import enrich_with_full_text  # noqa: E402
from bot.feeds import load_all_feeds  # noqa: E402
from bot.llm import GroqLLM  # noqa: E402
from bot.logging_setup import setup_logging  # noqa: E402
from bot.models import PostFormat, Topic  # noqa: E402
from bot.pipeline import filter_and_dedupe, select_candidates  # noqa: E402

MAX_CANDIDATES = 20


async def build_examples(out_dir: Path) -> dict:
    llm = GroqLLM()
    posted = PostedManager(config.db_file)
    done = {}
    stats = {"raw": 0, "candidates": 0, "assessed": 0, "rejected": 0, "generated": 0}
    try:
        raw = await load_all_feeds()
        stats["raw"] = len(raw)
        candidates = filter_and_dedupe(raw, posted)
        stats["candidates"] = len(candidates)
        ranked = select_candidates(candidates, posted)

        for item in ranked[:MAX_CANDIDATES]:
            if len(done) == len(PostFormat.ALL):
                break
            article = item.article
            await enrich_with_full_text(article)
            assessment = await assess_article(article, llm)
            stats["assessed"] += 1
            if not assessment.worth_publishing:
                stats["rejected"] += 1
                print(f"  ⛔ не стоит публикации: {article.title[:60]} — {assessment.reason}")
                continue
            fmt = assessment.post_format
            if fmt in done:
                continue
            generated = await generate_post(article, assessment, fmt, llm)
            if not generated:
                continue
            topic = Topic.detect(article.text)
            text = build_post_text(article, generated.body, fmt, topic)
            done[fmt] = {
                "title": article.title,
                "source": article.source,
                "link": article.link,
                "has_full_text": bool(article.full_text),
                "score": assessment.total,
                "score_source": assessment.source,
                "trigger": assessment.trigger,
                "reason": assessment.reason,
                "format": fmt,
                "quality": generated.quality.as_dict(),
                "text": text,
            }
            stats["generated"] += 1
            print(f"  ✅ {fmt}: {article.title[:60]}")
    finally:
        posted.close()
    return {"stats": stats, "examples": done}


def render_markdown(payload: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Примеры публикаций по форматам — {now}", "",
             "Сгенерировано на реальных RSS-материалах, в канал не отправлялось.", ""]
    for fmt in PostFormat.ALL:
        item = payload["examples"].get(fmt)
        lines.append(f"## {PostFormat.TITLES[fmt]} (`{fmt}`)")
        if not item:
            lines += ["_Нет подходящего материала в этом прогоне._", ""]
            continue
        lines += [
            f"- Источник: {item['source']}",
            f"- Оценка: {item['score']}/100 ({item['score_source']}), триггер: {item['trigger']}",
            f"- Полный текст статьи: {'да' if item['has_full_text'] else 'нет (только RSS)'}",
            f"- Самопроверка: {json.dumps(item['quality'], ensure_ascii=False)}",
            "", "```", item["text"], "```", "",
        ]
    lines += ["## Статистика прогона", "", "```json",
              json.dumps(payload["stats"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


async def main() -> int:
    setup_logging()
    out_dir = Path("docs/examples")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = await build_examples(out_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    (out_dir / f"examples-{stamp}.md").write_text(render_markdown(payload), encoding="utf-8")
    (out_dir / f"examples-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False))
    missing = [f for f in PostFormat.ALL if f not in payload["examples"]]
    print(f"форматов без примера: {missing}")
    return 0 if len(payload["examples"]) >= 3 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
