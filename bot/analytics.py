"""Аналитика публикаций (ТЗ §10): сбор просмотров и недельный отчёт.

Просмотры Bot API не отдаёт, поэтому берём их с публичной страницы поста
(t.me/<канал>/<id>). Если канал закрытый или страница не ответила — метрика
остаётся неизвестной (NULL), а не нулевой: ноль искажает средние.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence

from .config import config
from .models import PostFormat

logger = logging.getLogger(__name__)

VIEWS_RE = re.compile(r'tgme_widget_message_views">\s*([^<]+?)\s*<')
REPLIES_RE = re.compile(r'tgme_widget_message_replies"[^>]*?(?:data-count="(\d+)"|>\s*([^<]+?)\s*<)')
REACTIONS_RE = re.compile(r'tgme_reaction\b')
FORWARDS_RE = re.compile(r'tgme_widget_message_forwards"[^>]*?(?:data-count="(\d+)"|>\s*([^<]+?)\s*<)')

# Окна, которые требует ТЗ §10
WINDOWS = (
    ("views_1h", 1),
    ("views_6h", 6),
    ("views_24h", 24),
)
ENGAGEMENT_COLUMNS = ("forwards_24h", "reactions_24h", "comments_24h")


def parse_views(raw: str) -> Optional[int]:
    """«1.2K» → 1200, «23.4M» → 23400000, «1 234» → 1234. Мусор → None."""
    if raw is None:
        return None
    text = str(raw).strip().replace("\xa0", " ").replace("\u2009", " ").replace(" ", "")
    if not text:
        return None
    match = re.match(r"^([\d.,]+)\s*([KkMmКкМм])?$", text)
    if not match:
        return None
    number_raw, suffix = match.group(1), (match.group(2) or "").lower()
    # «1,234» — разделитель тысяч, «1.2» — десятичная дробь
    if "," in number_raw and "." in number_raw:
        number_raw = number_raw.replace(",", "")
    elif "," in number_raw:
        head, _, tail = number_raw.partition(",")
        number_raw = f"{head}.{tail}" if len(tail) in (1, 2) else number_raw.replace(",", "")
    try:
        value = float(number_raw)
    except ValueError:
        return None
    multiplier = {"k": 1000, "к": 1000, "m": 1000000, "м": 1000000}.get(suffix, 1)
    return int(round(value * multiplier))


def parse_page_metrics(html: str) -> Dict[str, Optional[int]]:
    """Просмотры, пересылки, реакции и комментарии со страницы поста."""
    result: Dict[str, Optional[int]] = {
        "views": None, "forwards": None, "reactions": None, "comments": None,
    }
    match = VIEWS_RE.search(html or "")
    if match:
        result["views"] = parse_views(match.group(1))

    match = FORWARDS_RE.search(html or "")
    if match:
        result["forwards"] = parse_views(match.group(1) or match.group(2))

    match = REPLIES_RE.search(html or "")
    if match:
        result["comments"] = parse_views(match.group(1) or match.group(2))

    if html:
        reactions = len(REACTIONS_RE.findall(html))
        result["reactions"] = reactions or None
    return result


async def fetch_message_metrics(username: str, message_id: int,
                                session=None) -> Dict[str, Optional[int]]:
    """Забрать метрики поста с публичной страницы. Пустой dict при неудаче."""
    if not username or not message_id:
        return {}
    url = f"https://t.me/{username}/{int(message_id)}?embed=1&mode=tme"
    try:
        import aiohttp
    except ImportError:  # pragma: no cover - зависимость есть в requirements
        logger.warning("⚠️ aiohttp недоступен, метрики не собрать")
        return {}

    own_session = session is None
    try:
        if own_session:
            session = aiohttp.ClientSession()
        timeout = aiohttp.ClientTimeout(total=config.stats_timeout)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with session.get(url, timeout=timeout, headers=headers) as response:
            if response.status != 200:
                logger.info(f"  ⚠️ t.me ответил {response.status} для {username}/{message_id}")
                return {}
            html = await response.text()
        return parse_page_metrics(html)
    except Exception as e:
        logger.info(f"  ⚠️ метрики {username}/{message_id} недоступны: {e}")
        return {}
    finally:
        if own_session and session is not None:
            await session.close()


def parse_db_time(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def due_windows(row: dict, now: Optional[datetime] = None) -> List[str]:
    """Какие окна уже наступили, но ещё не заполнены."""
    now = now or datetime.now(timezone.utc)
    published = parse_db_time(row.get("published_at"))
    if published is None:
        return []
    age_hours = (now - published).total_seconds() / 3600
    due = []
    for column, hours in WINDOWS:
        if row.get(column) is not None:
            continue
        if age_hours >= hours:
            due.append(column)
    return due


def effective_views(row: dict) -> Optional[int]:
    """Лучшая доступная цифра просмотров: 24ч → 6ч → 1ч."""
    for column, _ in reversed(WINDOWS):
        value = row.get(column)
        if value is not None:
            return int(value)
    return None


def _median_views(rows: Sequence[dict]) -> Optional[float]:
    values = [v for v in (effective_views(r) for r in rows) if v is not None]
    return float(median(values)) if values else None


def _rel(value: Optional[int], base: Optional[float]) -> Optional[float]:
    if value is None or not base:
        return None
    return value / base


def _group(rows: Iterable[dict], key: str) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "—"), []).append(row)
    return grouped


def popular_topics(rows: Sequence[dict], threshold: float = 1.3,
                   min_posts: int = 1) -> List[str]:
    """Темы с охватом выше среднего — для продолжения темы (ТЗ §6.3)."""
    base = _median_views(rows)
    if not base:
        return []
    result: List[str] = []
    for topic, group in _group(rows, "topic").items():
        values = [v for v in (effective_views(r) for r in group) if v is not None]
        if len(values) < min_posts:
            continue
        if median(values) >= base * threshold:
            result.append(topic)
    return result


def _stat_block(rows: Sequence[dict], base: Optional[float]) -> Dict[str, object]:
    measured = [r for r in rows if effective_views(r) is not None]
    views = [v for v in (effective_views(r) for r in measured) if v is not None]
    rels = [x for x in (_rel(v, base) for v in views) if x is not None]
    forwards = [int(r["forwards_24h"]) for r in rows if r.get("forwards_24h") is not None]
    return {
        "posts": len(rows),
        "measured": len(measured),
        "views": float(median(views)) if views else None,
        "rel": round(float(median(rels)), 2) if rels else None,
        "forwards": float(sum(forwards)) if forwards else None,
    }


def weekly_report(rows: Sequence[dict], days: Optional[int] = None,
                  now: Optional[datetime] = None) -> dict:
    """Отчёт по эффективности: темы, форматы, источники, часы, рекомендации."""
    days = days or config.report_days
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    fresh: List[dict] = []
    for row in rows:
        published = parse_db_time(row.get("published_at"))
        if published is None or published < since:
            continue
        item = dict(row)
        item["_published"] = published
        fresh.append(item)

    base = _median_views(fresh)
    min_posts = config.report_min_posts

    topics = []
    for topic, group in _group(fresh, "topic").items():
        block = _stat_block(group, base)
        block["topic"] = topic
        topics.append(block)
    topics.sort(key=lambda x: (x["rel"] is None, -(x["rel"] or 0), -x["posts"]))

    formats = []
    total = len(fresh) or 1
    for fmt, group in _group(fresh, "post_format").items():
        block = _stat_block(group, base)
        block["format"] = fmt
        block["share"] = round(len(group) / total, 2)
        block["target_share"] = PostFormat.TARGET_SHARE.get(fmt)
        formats.append(block)
    formats.sort(key=lambda x: (x["rel"] is None, -(x["rel"] or 0)))

    sources = []
    for source, group in _group(fresh, "source").items():
        block = _stat_block(group, base)
        block["source"] = source
        sources.append(block)
    sources.sort(key=lambda x: (x["rel"] is None, -(x["rel"] or 0), -x["posts"]))
    sources = [s for s in sources if s["posts"] >= 1][:8]

    hours = []
    for hour in range(24):
        group = [r for r in fresh
                 if _local_hour(r["_published"]) == hour]
        if not group:
            continue
        block = _stat_block(group, base)
        block["hour"] = hour
        hours.append(block)
    hours.sort(key=lambda x: (x["rel"] is None, -(x["rel"] or 0)))

    weak = [f for f in formats
            if f["posts"] >= min_posts and f["rel"] is not None and f["rel"] < 0.85]
    strong = [f for f in formats
              if f["posts"] >= min_posts and f["rel"] is not None and f["rel"] >= 1.1]

    report = {
        "days": days,
        "posts": len(fresh),
        "measured": sum(1 for r in fresh if effective_views(r) is not None),
        "median_views": round(base) if base else None,
        "topics": topics,
        "formats": formats,
        "sources": sources,
        "hours": hours,
        "weak_formats": weak,
        "strong_formats": strong,
        "recommendations": [],
    }
    report["recommendations"] = _recommend(report, min_posts)
    return report


def _local_hour(published: datetime) -> int:
    return (published + timedelta(hours=config.report_tz_offset)).hour


def _recommend(report: dict, min_posts: int) -> List[str]:
    """Рекомендации словами. Стратегию бот сам не меняет (ТЗ §10)."""
    out: List[str] = []

    for block in report["weak_formats"]:
        target = block.get("target_share")
        target_text = f", цель по ТЗ {int(target * 100)}%" if target else ""
        out.append(
            f"сократить долю «{block['format']}»: {block['posts']} публикаций, "
            f"охват {block['rel']:.2f} от медианы{target_text}"
        )

    for block in report["strong_formats"]:
        share = block.get("share") or 0
        target = block.get("target_share") or 0
        if share + 0.1 < target:
            out.append(
                f"увеличить долю «{block['format']}»: охват {block['rel']:.2f} от медианы, "
                f"факт {int(share * 100)}% против цели {int(target * 100)}%"
            )

    weak_topics = [t for t in report["topics"]
                   if t["posts"] >= min_posts and t["rel"] is not None and t["rel"] < 0.8]
    for block in weak_topics[:2]:
        out.append(f"реже брать тему «{block['topic']}»: охват {block['rel']:.2f} от медианы")

    best_hours = [h for h in report["hours"] if h["rel"] is not None and h["rel"] >= 1.2][:3]
    if best_hours:
        hours_text = ", ".join(f"{h['hour']:02d}:00" for h in best_hours)
        out.append(f"проверить время публикации: лучший охват в {hours_text} "
                   f"(МСК, UTC+{config.report_tz_offset})")

    if report["posts"] and report["measured"] < report["posts"] / 2:
        out.append("просмотры есть меньше чем у половины постов: проверить CHANNEL_USERNAME "
                   "и что канал публичный, иначе метрики остаются неизвестными")

    if not out:
        out.append("данных пока мало: нужно минимум "
                   f"{min_posts} публикаций на формат и на тему, чтобы делать выводы")
    return out


def render_report(report: dict) -> str:
    """Текст отчёта для /report и файла недельного прогона."""
    lines = [
        f"📊 Отчёт за {report['days']} дн.: {report['posts']} публикаций, "
        f"метрики есть у {report['measured']}",
    ]
    if report["median_views"]:
        lines.append(f"Медиана просмотров: {report['median_views']}")

    if report["formats"]:
        lines.append("\nФорматы (доля / охват к медиане):")
        for block in report["formats"]:
            rel = f"{block['rel']:.2f}" if block["rel"] is not None else "—"
            share = f"{int((block['share'] or 0) * 100)}%"
            lines.append(f"  • {block['format']}: {block['posts']} шт., {share}, охват {rel}")

    if report["topics"]:
        lines.append("\nТемы (охват к медиане):")
        for block in report["topics"][:6]:
            rel = f"{block['rel']:.2f}" if block["rel"] is not None else "—"
            lines.append(f"  • {block['topic']}: {block['posts']} шт., охват {rel}")

    if report["sources"]:
        lines.append("\nИсточники (охват к медиане):")
        for block in report["sources"][:5]:
            rel = f"{block['rel']:.2f}" if block["rel"] is not None else "—"
            lines.append(f"  • {block['source']}: {block['posts']} шт., охват {rel}")

    if report["hours"]:
        lines.append("\nЛучшие часы публикации (МСК, охват к медиане):")
        for block in report["hours"][:3]:
            rel = f"{block['rel']:.2f}" if block["rel"] is not None else "—"
            lines.append(f"  • {block['hour']:02d}:00 — охват {rel}")

    lines.append("\nРекомендации (применяет владелец, не бот):")
    for item in report["recommendations"]:
        lines.append(f"  • {item}")
    return "\n".join(lines)


@dataclass
class MetricsUpdate:
    """Результат похода за метриками одного поста."""

    message_id: int
    windows: Dict[str, int] = field(default_factory=dict)
    metrics: Dict[str, Optional[int]] = field(default_factory=dict)
    source: str = "unknown"


__all__ = [
    "parse_views", "parse_page_metrics", "fetch_message_metrics", "parse_db_time",
    "due_windows", "effective_views", "weekly_report", "render_report",
    "WINDOWS", "ENGAGEMENT_COLUMNS", "MetricsUpdate", "popular_topics",
]
