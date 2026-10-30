"""Оценка новости перед публикацией (ТЗ §3) — LLM поверх эвристик.

Модель отвечает по шести критериям теми же весами, что и эвристика этапа 1:
тематика 25, польза 25, актуальность 20 (считается по возрасту, не моделью),
новизна 15, обсуждение 10, надёжность источника 5.

Если модель недоступна или вернула мусор — остаётся эвристическая оценка:
бот не падает и не блокирует выпуск, но в лог пишется, что оценка эвристическая.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import PRIMARY_SOURCES, config
from .llm import GroqLLM, LLMError
from .models import Article, PostFormat, Topic
from .rating import (
    ScoreBreakdown,
    _freshness_score,
    _reliability_score,
    score_article,
)
from .textutils import TermMatcher

logger = logging.getLogger(__name__)

ASSESS_PROMPT = """Ты — технический редактор Telegram-канала про блокировки, VPN, сети и ИИ.
Оцени материал по шести критериям и предложи формат публикации.

ЗАГОЛОВОК: {title}
ИСТОЧНИК: {source}
ТЕКСТ (может быть неполным):
{text}

Верни СТРОГО JSON без пояснений:
{{
  "topic_relevance": 0-10,   // насколько тема соответствует каналу (блокировки, VPN, сети, безопасность, ИИ)
  "usefulness": 0-10,        // практическая польза: можно ли что-то применить или проверить
  "novelty": 0-10,           // новая информация или пересказ старого
  "discussion": 0-10,        // вызовет ли обсуждение, желание проверить, переслать
  "format": "howto|experiment|consequence|analysis|tool",
  "trigger": "угроза|неожиданность|конфликт|любопытство|выгода|срочность|разоблачение|сравнение|последствия",
  "worth_publishing": true|false,   // false — проходная новость без конфликта, неожиданности, последствий и пользы
  "reason": "короткое объяснение решения",
  "main_point": "главный подтверждённый факт одной фразой",
  "facts": ["3-6 фактов строго из текста, без домыслов"]
}}

Форматы:
- howto — практическое решение: проблема, причина, что делать, ограничения;
- experiment — эксперимент и проверка: если в источнике есть результаты теста, укажи, кто и в каких условиях проводил;
- consequence — последствия новости для пользователя;
- analysis — технический разбор механизма;
- tool — инструмент, клиент, сервис, репозиторий.

Не выдумывай факты: если данных нет — не добавляй. Никаких оценок без опоры на текст."""

VALID_FORMATS = set(PostFormat.ALL)
VALID_TRIGGERS = {
    "угроза", "неожиданность", "конфликт", "любопытство", "выгода",
    "срочность", "разоблачение", "сравнение", "последствия",
}

FORMAT_HINTS = {
    PostFormat.HOWTO: TermMatcher(
        ["что делать", "как ", "инструкц", "настро", "исправ", "провер", "руководств", "шаг"], name="fmt_howto"),
    PostFormat.EXPERIMENT: TermMatcher(
        ["тест", "протестировал", "сравн", "измер", "эксперимент", "проверк", "скорост"], name="fmt_exp"),
    PostFormat.ANALYSIS: TermMatcher(
        ["почему", "механизм", "разбор", "как работает", "причин", "устройств", "архитектур"],
        name="fmt_analysis"),
    PostFormat.TOOL: TermMatcher(
        ["клиент", "утилит", "инструмент", "сервис", "репозитор", "приложен", "плагин"], name="fmt_tool"),
    PostFormat.CONSEQUENCE: TermMatcher(
        ["обновлен", "выпуст", "запуст", "нововведен", "изменен", "перестал", "запрет"],
        name="fmt_consequence"),
}


@dataclass
class Assessment:
    breakdown: ScoreBreakdown
    post_format: str = PostFormat.CONSEQUENCE
    trigger: str = "последствия"
    worth_publishing: bool = True
    reason: str = ""
    main_point: str = ""
    facts: List[str] = field(default_factory=list)
    source: str = "heuristic"  # heuristic | llm | llm+fallback

    @property
    def total(self) -> int:
        return self.breakdown.total


def _clamp(value, low=0, high=10):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def parse_assessment(raw: str) -> Optional[Dict]:
    """Разобрать JSON модели, терпимо к обёрткам и тексту вокруг."""
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    fmt = str(data.get("format", "")).strip().lower()
    if fmt not in VALID_FORMATS:
        return None
    trigger = str(data.get("trigger", "")).strip().lower()
    facts = data.get("facts") or []
    if not isinstance(facts, list):
        facts = []
    return {
        "topic_relevance": _clamp(data.get("topic_relevance", 0)),
        "usefulness": _clamp(data.get("usefulness", 0)),
        "novelty": _clamp(data.get("novelty", 0)),
        "discussion": _clamp(data.get("discussion", 0)),
        "format": fmt,
        "trigger": trigger if trigger in VALID_TRIGGERS else "последствия",
        "worth_publishing": bool(data.get("worth_publishing", True)),
        "reason": str(data.get("reason", ""))[:300],
        "main_point": str(data.get("main_point", ""))[:500],
        "facts": [str(f)[:300] for f in facts[:8]],
    }


def heuristic_format(article: Article) -> str:
    text = article.text.lower()
    best, best_hits = PostFormat.CONSEQUENCE, 0
    for fmt, matcher in FORMAT_HINTS.items():
        hits = len(matcher.matches(text))
        if hits > best_hits:
            best, best_hits = fmt, hits
    return best


def build_breakdown(article: Article, data: Dict) -> ScoreBreakdown:
    breakdown = ScoreBreakdown()
    # округление половины вверх, чтобы 9/10 давало 23, а не 22 (банковское округление)
    breakdown.topic = int(data["topic_relevance"] * 2.5 + 0.5)
    breakdown.usefulness = int(data["usefulness"] * 2.5 + 0.5)
    breakdown.novelty = int(data["novelty"] * 1.5 + 0.5)
    breakdown.discussion = int(data["discussion"] * 1.0 + 0.5)
    breakdown.freshness, r_fresh = _freshness_score(article.age_hours())
    breakdown.reliability, r_rel = _reliability_score(article.source)
    breakdown.reasons = [
        f"LLM: тематика {data['topic_relevance']}/10, польза {data['usefulness']}/10, "
        f"новизна {data['novelty']}/10, обсуждение {data['discussion']}/10",
        f"актуальность: {r_fresh}",
        f"надёжность: {r_rel}",
    ]
    return breakdown


def heuristic_assessment(article: Article, reason: str = "эвристика") -> Assessment:
    breakdown = score_article(article)
    breakdown.reasons = [f"ЭВРИСТИКА ({reason})"] + list(breakdown.reasons)
    return Assessment(
        breakdown=breakdown,
        post_format=heuristic_format(article),
        trigger="последствия",
        worth_publishing=breakdown.total >= config.queue_min_score,
        reason=reason,
        source="heuristic",
    )


async def assess_article(article: Article, llm: Optional[GroqLLM] = None) -> Assessment:
    """Оценка материала моделью с откатом на эвристику."""
    llm = llm or GroqLLM()
    text = article.full_text or article.summary or ""
    prompt = ASSESS_PROMPT.format(
        title=article.title,
        source=article.source,
        text=text[: config.max_fulltext_chars],
    )
    try:
        raw = await llm.complete(prompt, max_tokens=700, temperature=0.2, json_mode=True)
    except LLMError as e:
        logger.warning(f"  ⚠️ Оценка LLM недоступна ({e}), считаю эвристикой")
        return heuristic_assessment(article, "LLM недоступна")

    data = parse_assessment(raw)
    if not data:
        logger.warning("  ⚠️ Оценка LLM не разобрана, считаю эвристикой")
        return heuristic_assessment(article, "некорректный JSON модели")

    worth = data["worth_publishing"] and data["topic_relevance"] >= config.llm_min_worth
    return Assessment(
        breakdown=build_breakdown(article, data),
        post_format=data["format"],
        trigger=data["trigger"],
        worth_publishing=worth,
        reason=data["reason"],
        main_point=data["main_point"],
        facts=data["facts"],
        source="llm",
    )


def format_shares(recent_formats: List[str]) -> Dict[str, float]:
    """Фактические доли форматов за последнее время (для рекомендаций этапа 4)."""
    total = len([f for f in recent_formats if f])
    if not total:
        return {}
    shares: Dict[str, float] = {}
    for fmt in recent_formats:
        if not fmt:
            continue
        shares[fmt] = shares.get(fmt, 0) + 1
    return {fmt: count / total for fmt, count in shares.items()}


__all__ = [
    "Assessment",
    "assess_article",
    "heuristic_assessment",
    "heuristic_format",
    "parse_assessment",
    "build_breakdown",
    "Topic",
    "PRIMARY_SOURCES",
]
