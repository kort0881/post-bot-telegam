"""Единый рейтинг кандидатов (ТЗ §3).

Веса: тематика 25, польза 25, актуальность 20, новизна 15, обсуждение 10,
надёжность источника 5. Сумма — 100.

Оценка на этом этапе эвристическая и полностью объяснимая: LLM-классификация
подключается отдельным шагом и использует её как приоритет, а не как замену.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .config import PRIMARY_SOURCES, config
from .filters import (
    AI_STRONG,
    BLOCK,
    LISTICLE_MARKERS,
    PROFILE,
    TECHNICAL_MARKERS,
)
from .models import Article
from .textutils import TermMatcher

# Признаки практической пользы: инструкция, что делать, диагностика
USEFUL = TermMatcher(
    [
        "что делать", "как ", "инструкц", "провер", "настро", "шаг",
        "способ", "решен", "исправ", "восстанов", "параметр", "включ",
        "отключ", "руководств", "гайд", "совет", "тест", "сравн",
        "how to", "guide", "fix", "setup", "config",
    ],
    name="useful",
)

NOT_USEFUL = TermMatcher(
    ["презентац", "объявил о", "приглаша", "конференц", "выставк", "интервью",
     "мнение", "прогноз", "колонк"],
    name="not_useful",
)

NOVELTY_POSITIVE = TermMatcher(
    ["впервые", "новый", "новая", "новое", "запустил", "выпустил", "представил",
     "обнаруж", "раскрыл", "анонсир", "внедрил", "открыл", "разработал"],
    name="novelty_pos",
)

NOVELTY_NEGATIVE = TermMatcher(
    ["напомним", "как известно", "ранее сообщал", "повторн", "снова",
     "очередн", "всё ещё", "все еще"],
    name="novelty_neg",
)

DISCUSSION = TermMatcher(
    ["запрет", "суд", "блокиров", "сбой", "утечк", "ошибк", "конфликт",
     "критик", "жалоб", "скандал", "расследован", "обвин", "штраф",
     "ограничен", "замедлен", "отключ", "перестал"],
    name="discussion",
)

# Надёжность источника: 5 — проверенные редакции, 4 — известные профильные,
# 3 — остальные, 2 — соцсети/агрегаторы.
RELIABLE_SOURCES = {
    "Роскомсвобода": 5,
    "Habr AI": 4,
    "Kod.ru": 4,
    "ComNews": 4,
    "Ars Technica AI": 4,
    "The Decoder": 4,
    "MIT Tech Review AI": 4,
    "Hacker News": 3,
    "Google News (Блокировки)": 3,
}

UNRELIABLE_SOURCES = {"РКН": 2}


@dataclass
class ScoreBreakdown:
    topic: int = 0
    usefulness: int = 0
    freshness: int = 0
    novelty: int = 0
    discussion: int = 0
    reliability: int = 0
    reasons: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.topic + self.usefulness + self.freshness + self.novelty + self.discussion + self.reliability

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "usefulness": self.usefulness,
            "freshness": self.freshness,
            "novelty": self.novelty,
            "discussion": self.discussion,
            "reliability": self.reliability,
            "total": self.total,
            "reasons": list(self.reasons),
        }


def priority_band(total: int) -> str:
    if total >= 80:
        return "priority"
    if total >= 65:
        return "publish"
    if total >= 50:
        return "queue"
    return "reject"


def _topic_score(text: str) -> tuple:
    profile = len(PROFILE.matches(text))
    blocks = len(BLOCK.matches(text))
    strong_ai = len(AI_STRONG.matches(text))
    if blocks >= 2 or profile >= 4:
        return 25, "профильная тема канала"
    if blocks >= 1 or profile >= 2:
        return 22, "профильная тема канала"
    if profile == 1 or strong_ai >= 1:
        return 15, "тематика рядом"
    return 5, "слабое соответствие тематике"


def _usefulness_score(text: str) -> tuple:
    hits = USEFUL.matches(text)
    negatives = NOT_USEFUL.matches(text)
    score = 8 + 5 * len(hits) - 4 * len(negatives)
    if TECHNICAL_MARKERS.any(text):
        score += 5
    return max(0, min(25, score)), (", ".join(hits[:3]) or "нет маркеров пользы")


def _freshness_score(age_hours: float) -> tuple:
    if age_hours <= 3:
        return 20, "свежая"
    if age_hours <= 8:
        return 17, "свежая"
    if age_hours <= 24:
        return 13, "сутки"
    if age_hours <= 72:
        return 9, "трое суток"
    if age_hours <= 168:
        return 5, "неделя"
    return 2, "старая"


def _novelty_score(text: str) -> tuple:
    positive = NOVELTY_POSITIVE.matches(text)
    negative = NOVELTY_NEGATIVE.matches(text)
    score = 6 + 4 * len(positive) - 3 * len(negative)
    if LISTICLE_MARKERS.any(text):
        score -= 4
    return max(0, min(15, score)), (", ".join(positive[:3]) or "без явной новизны")


def _discussion_score(text: str) -> tuple:
    hits = DISCUSSION.matches(text)
    return min(10, 2 + 3 * len(hits)), (", ".join(hits[:3]) or "низкий потенциал обсуждения")


def _reliability_score(source: str) -> tuple:
    if source in RELIABLE_SOURCES:
        return RELIABLE_SOURCES[source], f"источник {source}"
    if source in PRIMARY_SOURCES:
        return 4, f"источник {source}"
    if source in UNRELIABLE_SOURCES:
        return UNRELIABLE_SOURCES[source], f"источник {source}"
    return 3, f"источник {source}"


def score_article(article: Article, now: Optional[datetime] = None) -> ScoreBreakdown:
    text = article.text.lower()
    breakdown = ScoreBreakdown()

    breakdown.topic, r = _topic_score(text)
    breakdown.reasons.append(f"тематика: {r}")
    breakdown.usefulness, r = _usefulness_score(text)
    breakdown.reasons.append(f"польза: {r}")
    breakdown.freshness, r = _freshness_score(article.age_hours(now))
    breakdown.reasons.append(f"актуальность: {r}")
    breakdown.novelty, r = _novelty_score(text)
    breakdown.reasons.append(f"новизна: {r}")
    breakdown.discussion, r = _discussion_score(text)
    breakdown.reasons.append(f"обсуждение: {r}")
    breakdown.reliability, r = _reliability_score(article.source)
    breakdown.reasons.append(f"надёжность: {r}")
    return breakdown


@dataclass
class RankedCandidate:
    article: Article
    score: ScoreBreakdown
    penalty: int = 0
    penalty_reasons: List[str] = field(default_factory=list)

    @property
    def effective(self) -> int:
        return max(0, self.score.total - self.penalty)

    @property
    def band(self) -> str:
        return priority_band(self.score.total)


def apply_history_penalties(
    ranked: List[RankedCandidate],
    recent_sources: List[str],
    recent_topics: List[str],
) -> List[RankedCandidate]:
    """Мягкие штрафы за однообразие: не переупорядочивают рейтинг, а сдвигают балл."""
    from .models import Topic

    recent_sources = [s for s in recent_sources if s]
    last_sources = recent_sources[:config.source_min_posts_between]
    source_counts = {}
    for src in recent_sources[:config.rotation_history_size]:
        source_counts[src] = source_counts.get(src, 0) + 1

    last_topics = recent_topics[:config.max_same_topic_in_row]

    for item in ranked:
        src = item.article.source
        if src in last_sources:
            item.penalty += 8
            item.penalty_reasons.append(f"источник {src} был в последних постах")
        if source_counts.get(src, 0) >= config.source_max_in_window:
            item.penalty += 5
            item.penalty_reasons.append(f"источник {src} часто публиковался")

        topic = Topic.detect(item.article.text)
        if last_topics and all(t == topic for t in last_topics):
            item.penalty += 6
            item.penalty_reasons.append(f"тема {topic} повторяется подряд")

    return ranked


def rank_candidates(
    articles: List[Article],
    recent_sources: Optional[List[str]] = None,
    recent_topics: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> List[RankedCandidate]:
    now = now or datetime.now(timezone.utc)
    ranked = [RankedCandidate(article=a, score=score_article(a, now)) for a in articles]
    apply_history_penalties(ranked, recent_sources or [], recent_topics or [])
    ranked.sort(key=lambda c: (c.effective, c.article.published), reverse=True)
    return ranked


def diversify(
    ranked: List[RankedCandidate],
    max_per_source: int = 2,
    max_per_topic: int = 2,
) -> List[RankedCandidate]:
    """Наложить ограничения по разнообразию, сохранив исходный рейтинг.

    Материал сверх квоты не теряется: он попадает в конец списка, а не в топ —
    иначе принцип «качество выше разнообразия» нарушается.
    """
    from .models import Topic

    kept: List[RankedCandidate] = []
    deferred: List[RankedCandidate] = []
    per_source = {}
    per_topic = {}

    for item in ranked:
        src = item.article.source
        topic = Topic.detect(item.article.text)
        if per_source.get(src, 0) >= max_per_source or per_topic.get(topic, 0) >= max_per_topic:
            deferred.append(item)
            continue
        per_source[src] = per_source.get(src, 0) + 1
        per_topic[topic] = per_topic.get(topic, 0) + 1
        kept.append(item)

    deferred.sort(key=lambda c: (c.effective, c.article.published), reverse=True)
    return kept + deferred
