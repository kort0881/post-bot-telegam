"""Получение полного текста статьи (ТЗ §4).

Что делаем: тянем HTML, выкидываем меню/рекламу/скрипты, оставляем связные абзацы.
Если текста мало или он похож на навигацию — возвращаем None и работаем с RSS-описанием:
пересказ тогда честно ограничен тем, что есть в источнике.
"""
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from .config import HEADERS, config

logger = logging.getLogger(__name__)

DROP_TAGS = (
    "script", "style", "noscript", "iframe", "svg", "form", "nav", "footer",
    "header", "aside", "button", "time", "figure", "figcaption",
)

DROP_CLASS_PATTERNS = re.compile(
    r"(advert|promo|banner|subscribe|newsletter|social|share|comment|cookie|"
    r"related|recommend|popup|modal|breadcrumb|menu|nav|sidebar|widget|teaser)",
    re.IGNORECASE,
)

CONTENT_SELECTORS = (
    "article",
    "[itemprop='articleBody']",
    "div.article-body",
    "div.post-content",
    "div.entry-content",
    "div.content",
    "main",
    "#content",
)

MIN_PARAGRAPH_CHARS = 70


def _clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class") or [])
        ident = tag.get("id") or ""
        if DROP_CLASS_PATTERNS.search(classes) or DROP_CLASS_PATTERNS.search(ident):
            tag.decompose()
    return soup


def extract_from_html(html: str, min_chars: Optional[int] = None) -> Optional[str]:
    """Достать основной текст из HTML. None — если текста недостаточно."""
    min_chars = min_chars if min_chars is not None else config.min_fulltext_chars
    if not html or len(html) < 200:
        return None

    soup = BeautifulSoup(html, "lxml")
    soup = _clean_soup(soup)

    container = None
    for selector in CONTENT_SELECTORS:
        found = soup.select_one(selector)
        if found:
            container = found
            break
    container = container or soup.body or soup

    paragraphs: List[str] = []
    for p in container.find_all(["p", "li", "blockquote"]):
        text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
        if len(text) < MIN_PARAGRAPH_CHARS:
            continue
        # абзац, забитый ссылками, — это навигация или подвал
        link_chars = sum(len(a.get_text(strip=True)) for a in p.find_all("a"))
        if link_chars and link_chars / max(len(text), 1) > 0.6:
            continue
        paragraphs.append(text)

    # абзацев может не быть вовсе (лендинги, короткие заметки) — берём текст блока
    if not paragraphs:
        text = re.sub(r"\s+", " ", container.get_text(" ", strip=True))
        if len(text) >= min_chars:
            paragraphs = [text]

    deduped: List[str] = []
    for p in paragraphs:
        if p not in deduped:
            deduped.append(p)

    result = "\n\n".join(deduped).strip()
    if len(result) < min_chars:
        return None
    # текст без пробелов/слов — это не статья (склейка скриптов, json, base64)
    if len(re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", result)) < 30:
        return None
    return result[: config.max_fulltext_chars]


async def fetch_full_text(url: str) -> Optional[str]:
    """Скачать страницу и вернуть полный текст или None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    timeout = aiohttp.ClientTimeout(total=config.extract_timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=HEADERS, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.info(f"  📄 полный текст: HTTP {resp.status} ({url[:60]})")
                    return None
                content_type = resp.headers.get("Content-Type", "")
                if "html" not in content_type.lower():
                    logger.info(f"  📄 полный текст: не HTML ({content_type[:40]})")
                    return None
                html = await resp.text(errors="ignore")
    except Exception as e:
        logger.info(f"  📄 полный текст не получен: {e}")
        return None

    text = extract_from_html(html)
    if text:
        logger.info(f"  📄 полный текст: {len(text)} симв. ({url[:60]})")
    else:
        logger.info(f"  📄 полный текст короче порога ({url[:60]})")
    return text


async def enrich_with_full_text(article, session: Optional[aiohttp.ClientSession] = None):
    """Заполнить article.full_text, если удалось. Молча оставить None иначе."""
    if article.full_text:
        return article
    text = await fetch_full_text(article.link)
    if text:
        article.full_text = text
    return article
