"""Сюжетный слой дублей: один и тот же сюжет в разных изданиях.

Заголовочные пороги (`TITLE_SIMILARITY`, `NGRAM_SIMILARITY`, `JACCARD_SIMILARITY`)
ловят перепечатку одного текста, но не пересказ. 12–19.11.2026 канал семь раз
выпустил материал про «РКН и блокировки с ИИ» — каждый раз другое издание,
другой заголовок, другой id в базе, поэтому ни `is_duplicate`, ни кулдаун на
субъекта (`rkn` в CORE_ENTITIES разрешает 2 поста за 48 ч) не срабатывали.

Здесь сюжет сравнивается по словам заголовка и описания с IDF-взвешиванием:
редкое слово («роскомнадзор», «5090») весит больше частого («блокировк», «vpn»).
Обязательное условие отказа — общий РЕДКИЙ токен. Без него близость дают
случайные совпадения: «VPN-клиент на 300 МБ» и «Шадаев про SIM-карты» дают 0.43
на живой базе, хотя это разные сюжеты. Порог и окно редкости замерены на 200
последних постах канала 19.11.2026: пары «один сюжет» — 0.22…0.48, чужие — до 0.05.
"""
import math
import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .textutils import STOP_WORDS

# Обрывки HTML-сущностей и служебных слов из RSS: в заголовках встречаются как «слова».
ARTIFACT_TOKENS = frozenset({
    "nbsp", "laquo", "raquo", "quot", "amp", "mdash", "ndash", "hellip", "rsquo", "lsquo",
})

TOKEN_RE = re.compile(r"[a-zа-яё0-9]+")
STEM_LEN = 6


def story_tokens(text: str) -> Counter:
    """Значимые слова текста, усечённые до 6 символов: «блокировки» → «блокир»."""
    counts: Counter = Counter()
    for word in TOKEN_RE.findall((text or "").lower()):
        if len(word) <= 2 or word in STOP_WORDS or word in ARTIFACT_TOKENS:
            continue
        counts[word[:STEM_LEN]] += 1
    return counts


def build_df(docs: Sequence[Counter]) -> Dict[str, int]:
    """Сколько документов содержат токен. Для IDF: редкое слово = больший вес."""
    df: Dict[str, int] = {}
    for doc in docs:
        for token in doc:
            df[token] = df.get(token, 0) + 1
    return df


def _weight(token: str, df: Dict[str, int], total: int) -> float:
    document_frequency = df.get(token, 0)
    if document_frequency <= 0:
        document_frequency = 1
    return math.log(1 + total / document_frequency) ** 2


def _weighted_cosine(left: Counter, right: Counter, df: Dict[str, int], total: int) -> float:
    common = set(left) & set(right)
    if not common:
        return 0.0
    numerator = sum(left[t] * right[t] * _weight(t, df, total) for t in common)
    norm_left = math.sqrt(sum(v * v * _weight(t, df, total) for t, v in left.items()))
    norm_right = math.sqrt(sum(v * v * _weight(t, df, total) for t, v in right.items()))
    if not norm_left or not norm_right:
        return 0.0
    return numerator / (norm_left * norm_right)


def find_story(
    candidate: Counter,
    docs: Sequence[Counter],
    titles: Sequence[str],
    df: Dict[str, int],
    total: int,
    min_score: float = 0.25,
    rare_df: int = 3,
) -> Optional[dict]:
    """Самый близкий опубликованный сюжет или None.

    Отказ только при двух условиях сразу: взвешенная близость выше `min_score`
    И есть общий токен, встречающийся не более `rare_df` раз в корпусе.
    """
    if not candidate or not docs:
        return None
    best: Optional[dict] = None
    for index, doc in enumerate(docs):
        score = _weighted_cosine(candidate, doc, df, total)
        if score < min_score:
            continue
        shared = set(candidate) & set(doc)
        rare = sorted((t for t in shared if df.get(t, total) <= rare_df),
                      key=lambda token: df.get(token, total))
        if not rare:
            continue
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "rare": rare[:4],
                "title": titles[index] if index < len(titles) else "",
            }
    return best


def normalise_rows(rows: Sequence[Tuple[str, str]]) -> Tuple[List[Counter], List[str]]:
    """(заголовок, описание) → документы и заголовки в том же порядке."""
    docs: List[Counter] = []
    titles: List[str] = []
    for title, summary in rows:
        titles.append(title or "")
        docs.append(story_tokens(f"{title or ''} {summary or ''}"))
    return docs, titles


# ===== правила сюжета =====
# Лексическая близость ловит пересказ одного события, но не серию материалов
# с разными фактами про одно и то же: 1750 «2,27 млрд на ИИ» и 1753 «запустил ИИ
# для блокировки» дают 0.177 — ниже любого безопасного порога. Поэтому у
# повторяющихся тем канала есть явное правило: набор групп слов, каждая из
# которых обязана найтись, и окно с лимитом.
STORY_RULES: Dict[str, dict] = {
    "rkn_blocks": {
        "name": "цензура и блокировки в РФ",
        "groups": (
            ("блокиров", "фильтр", "цензур", "запрещ", "впн", "vpn", "тспу", "dpi", "обход"),
            ("росси", "ркн", "роскомнадзор", "roskomnadzor", "мнцифры", "mintsifry", "шадаев"),
        ),
        # Материалы про VPN-продукты и разработчиков — не политика блокировок.
        # Их ловит лексическая близость, а правило трогать не должно: иначе
        # «экс-сотрудник РКН написал VPN-клиент» закрывает тему на 72 часа.
        "negative": ("написал", "написа", "разработа", "сделал", "выпустил", "репозитор",
                     "клиент", "плагин", "подписк", "обзор"),
    },
}

# Слова короче не ищем: «ии» и «рф» дают ложные совпадения в правилах.
RULE_MIN_TERM = 3


def _rule_hit(text: str, groups: Sequence[Sequence[str]],
              negative: Sequence[str] = ()) -> bool:
    lowered = (text or "").lower()
    if any(term in lowered for term in negative):
        return False
    for group in groups:
        if not any(term in lowered for term in group if len(term) >= RULE_MIN_TERM):
            return False
    return True


def story_rule(text: str) -> Optional[str]:
    """Ключ сюжета, если текст попадает в правило темы, иначе None."""
    for key, rule in STORY_RULES.items():
        if _rule_hit(text, rule["groups"], rule.get("negative", ())):
            return key
    return None


__all__ = [
    "ARTIFACT_TOKENS",
    "STORY_RULES",
    "build_df",
    "find_story",
    "normalise_rows",
    "story_rule",
    "story_tokens",
]
