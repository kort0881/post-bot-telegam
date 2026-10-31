"""Вовлечение (ТЗ §6): серийные рубрики, просьбы о проверке, сбор случаев, опросы.

Принципы, которые здесь зашиты:
- вовлечение встроено в содержание, а не «А что вы думаете?» в конце;
- бот не утверждает, что провёл тест или опрос аудитории, которого не было;
- рубрики не идут подряд и не повторяются чаще кулдауна;
- опрос появляется только тогда, когда он собирает полезные данные по теме материала.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from .config import config
from .models import Article, PostFormat, Topic

logger = logging.getLogger(__name__)

# Оформление серийных рубрик (ТЗ §6.5): узнаваемый заголовок без визуального шума
RUBRIC_EMOJI = {
    "Проверка недели": "🔬",
    "Что делать": "🧰",
    "Что изменилось": "🔄",
    "Разбор блокировки": "🧩",
    "Полезная находка": "🔧",
}

# Рубрики — не формат, а серия: одна и та же не должна идти подряд
RUBRIC_MIN_GAP_POSTS = 2


@dataclass
class RubricPlan:
    """Рубрика, под которой выйдет материал. header — готовая первая строка."""

    name: str
    header: str


def _post_rubric(post: dict) -> str:
    return str(post.get("rubric") or "").strip()


def _post_date(post: dict) -> Optional[datetime]:
    raw = post.get("date") or post.get("posted_date")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def rubric_last_seen_days(name: str, recent_posts: Sequence[dict],
                          now: Optional[datetime] = None) -> Optional[float]:
    """Сколько дней назад рубрика выходила последний раз. None — не выходила."""
    now = now or datetime.now(timezone.utc)
    for post in recent_posts:
        if _post_rubric(post) != name:
            continue
        date = _post_date(post)
        if date is None:
            return None
        return max(0.0, (now - date).total_seconds() / 86400)
    return None


def choose_rubric(fmt: str, recent_posts: Sequence[dict],
                  now: Optional[datetime] = None) -> Optional[RubricPlan]:
    """Рубрика для формата или None, если показывать её рано (ТЗ §6.5)."""
    name = PostFormat.RUBRIC.get(fmt, "")
    if not name:
        return None

    tail = [_post_rubric(p) for p in recent_posts[:RUBRIC_MIN_GAP_POSTS]]
    if name in tail:
        return None

    days = rubric_last_seen_days(name, recent_posts, now)
    if days is not None and days < config.rubric_cooldown_days:
        return None

    emoji = RUBRIC_EMOJI.get(name, "📌")
    return RubricPlan(name=name, header=f"{emoji} {name}")


@dataclass
class EngagementRequest:
    """Просьба к читателю, которую модель встраивает в текст (ТЗ §6.1–6.3)."""

    kind: str        # hypothesis | cases | continue
    instruction: str


HYPOTHESIS_TERMS = (
    "перестал", "не работает", "не подключа", "сбой", "отказ",
    "обновлен", "блокир", "замедл", "ошибк", "обрыв",
)
CASE_TERMS = (
    "массов", "жалоб", "пользовател", "провайдер", "оператор", "регион",
    "у части", "сообщают",
)
CLIENT_TERMS = ("клиент", "приложен", "karing", "happ", "v2rayn", "sing-box", "hiddify")


def _matches(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def engagement_request(article: Article, post_format: str,
                       popular_topics: Optional[Sequence[str]] = None) -> Optional[EngagementRequest]:
    """Одна встроенная механика вовлечения или ничего.

    Выбирается по материалу, а не случайно: гипотеза — когда есть сбой/обновление,
    сбор случаев — когда источник говорит о массовости, продолжение — когда тема
    уже показала охват выше среднего.
    """
    text = f"{article.title} {article.summary or ''}"
    topic = Topic.detect(article.text)
    popular = set(popular_topics or [])

    if post_format in (PostFormat.HOWTO, PostFormat.ANALYSIS) and _matches(text, HYPOTHESIS_TERMS):
        return EngagementRequest(
            kind="hypothesis",
            instruction=(
                "В конце — одна проверяемая просьба: что именно читателю проверить и какой "
                "результат сообщить (конфигурация, клиент, провайдер, наблюдаемое поведение). "
                "Пиши как гипотезу: «похоже, дело в …», «проверим на нескольких конфигурациях». "
                "Не утверждай, что проверка уже проведена и что редакция её делала."
            ),
        )

    if post_format == PostFormat.CONSEQUENCE and topic in (Topic.BYPASS, Topic.BLOCK) \
            and _matches(text, CASE_TERMS):
        return EngagementRequest(
            kind="cases",
            instruction=(
                "В финале — одна просьба прислать свой случай: клиент, провайдер/регион и что "
                "именно происходит. Нужно понять, проблема массовая или зависит от провайдера. "
                "Без «поделитесь мнением» и без обещаний, которых бот не выполнит."
            ),
        )

    if topic in popular and post_format in (PostFormat.ANALYSIS, PostFormat.EXPERIMENT):
        return EngagementRequest(
            kind="continue",
            instruction=(
                "Свяжи материал с темой, которая уже выходила в канале: одной фразой, без "
                "пересказа прошлого поста. Если новых фактов нет — не упоминай прошлый материал."
            ),
        )

    return None


def apply_engagement(body: str, request: Optional[EngagementRequest]) -> str:
    """Проверить, что механика вовлечения действительно попала в текст.

    Текст не переписывается: вовлечение заложено в промпт, здесь фиксируется,
    есть ли в готовом тексте просьба к читателю (нужно для логов и тестов).
    """
    if request is None:
        return body
    lowered = body.lower()
    markers = {
        "hypothesis": ("провер", "сообщ", "напиш", "уточн"),
        "cases": ("сообщ", "напиш", "пришл", "укаж"),
        "continue": ("возвраща", "продолж", "как писали", "в прошл"),
    }[request.kind]
    if not any(marker in lowered for marker in markers):
        logger.warning(f"  ⚠️ вовлечение ({request.kind}) не попало в текст")
    return body


# ===== опросы (ТЗ §6.4) =====

@dataclass
class PollSpec:
    question: str
    options: List[str]
    multiple: bool = False
    anonymous: bool = True


POLL_TEMPLATES = {
    "protocol": (
        "Какой протокол у вас работает в этой сети?",
        ["VLESS/Reality", "XHTTP", "Hysteria2", "Ни один не подключается"],
    ),
    "client": (
        "На каком клиенте возникла проблема?",
        ["Karing", "v2rayN / HAPP", "sing-box", "Другой"],
    ),
    "recovered": (
        "После обновления соединение восстановилось?",
        ["Да, всё работает", "Частично", "Нет, не подключается", "Ещё не проверял"],
    ),
}


def build_poll(article: Article, post_format: str, state: Optional[dict] = None) -> Optional[PollSpec]:
    """Опрос, привязанный к теме материала, или None.

    state: {"polls_today": int, "posts_since_last_poll": int} — предохранители,
    чтобы опросы не превратились в способ поднять активность.
    """
    if not config.polls_enabled:
        return None
    state = state or {}
    if int(state.get("polls_today", 0)) >= config.poll_daily_limit:
        return None
    if int(state.get("posts_since_last_poll", 10 ** 6)) < config.poll_min_posts_between:
        return None

    text = article.text.lower()
    topic = Topic.detect(article.text)

    if "обновлен" in text or "после обновления" in text:
        key = "recovered"
    elif any(term in text for term in CLIENT_TERMS):
        key = "client"
    elif topic in (Topic.BYPASS, Topic.BLOCK) and any(
            term in text for term in ("vless", "xhttp", "reality", "протокол", "vpn", "proxy", "прокси")):
        key = "protocol"
    else:
        return None

    question, options = POLL_TEMPLATES[key]
    return PollSpec(question=question, options=list(options))


__all__ = [
    "RubricPlan", "choose_rubric", "rubric_last_seen_days",
    "EngagementRequest", "engagement_request", "apply_engagement",
    "PollSpec", "build_poll", "POLL_TEMPLATES",
]
