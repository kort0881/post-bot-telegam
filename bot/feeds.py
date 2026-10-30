"""Загрузка RSS-лент: таймауты, повторы, изоляция ошибок источника."""
import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import List

import aiohttp
import feedparser

from .config import HEADERS, RSS_FEEDS, config
from .models import Article
from .textutils import clean_html

logger = logging.getLogger(__name__)


async def _fetch_once(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers=HEADERS) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.text()


async def fetch_feed(url: str, source: str, retries: int = 2) -> List[Article]:
    await asyncio.sleep(random.uniform(0.3, 1.5))
    timeout = aiohttp.ClientTimeout(total=config.http_timeout)
    content = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(retries):
            try:
                content = await _fetch_once(session, url)
                break
            except asyncio.TimeoutError:
                logger.warning(f"  ⚠️ {source}: Timeout (попытка {attempt + 1})")
            except Exception as e:
                logger.warning(f"  ⚠️ {source}: {e} (попытка {attempt + 1})")
            if attempt + 1 < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
    if not content:
        return []

    try:
        feed = await asyncio.to_thread(feedparser.parse, content)
    except Exception as e:
        logger.warning(f"  ⚠️ {source}: разбор RSS упал: {e}")
        return []

    articles = []
    for entry in feed.entries[:20]:
        link = entry.get('link', '').strip()
        title = entry.get('title', '').strip()
        summary = re.sub(r'<[^>]+>', '', entry.get('summary', entry.get('description', '')).strip())
        if not link or not title or len(title) < 15:
            continue
        pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
        published = datetime(*pub_date[:6], tzinfo=timezone.utc) if pub_date else datetime.now(timezone.utc)

        # RSS full content (content:encoded) — если он есть, это лучший источник текста
        full_text = None
        for block in (entry.get('content') or []):
            candidate = clean_html(block.get('value') or "")
            if len(candidate) >= config.min_fulltext_chars:
                full_text = candidate[: config.max_fulltext_chars]
                break
        if full_text is None and len(summary) >= config.min_fulltext_chars:
            full_text = summary[: config.max_fulltext_chars]

        articles.append(Article(title=title, summary=summary, link=link,
                                source=source, published=published, full_text=full_text))
    logger.info(f"  ✅ {source}: {len(articles)}")
    return articles


async def load_all_feeds() -> List[Article]:
    logger.info("📥 Загрузка RSS...")
    tasks = [fetch_feed(url, source) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles: List[Article] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"  ⚠️ {RSS_FEEDS[i][1]}: {result}")
        elif result:
            all_articles.extend(result)
    logger.info(f"📦 Всего: {len(all_articles)}")
    return all_articles
