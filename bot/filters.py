"""Фильтрация новостей: тематика, мусор, реклама, вакансии.

Ключевые изменения против исходной логики (ТЗ §2):
- поиск терминов по границам слов, а не подстрокой («работает» больше не вакансия);
- «обзор»/«тест»/«review» сами по себе не отбрасывают материал: профильная тема
  (блокировки, VPN, прокси, сети, безопасность) проходит проверку;
- отсекаются коммерческие подборки и скидочные посты, а не технические тесты.
"""
import logging
from typing import List, Tuple

from .config import (
    AI_KEYWORDS_STRONG,
    AI_KEYWORDS_WEAK,
    BLOCK_KEYWORDS,
    BUSINESS_EXCLUDE,
    COMMERCIAL_MARKERS,
    GAMES_EXCLUDE,
    JUNK_PATTERNS,
    PROFILE_KEYWORDS,
    PROMO_PATTERNS,
    REVIEW_KEYWORDS,
    RUSSIA_KEYWORDS,
    config,
)
from .models import Article
from .textutils import TermMatcher

logger = logging.getLogger(__name__)

AI_STRONG = TermMatcher(AI_KEYWORDS_STRONG, name="ai_strong")
AI_WEAK = TermMatcher(AI_KEYWORDS_WEAK, name="ai_weak")
BLOCK = TermMatcher(BLOCK_KEYWORDS, name="block")
PROFILE = TermMatcher(PROFILE_KEYWORDS, name="profile")
GAMES = TermMatcher(GAMES_EXCLUDE, name="games")
BUSINESS = TermMatcher(BUSINESS_EXCLUDE, name="business")
PROMO = TermMatcher(PROMO_PATTERNS, name="promo")
COMMERCIAL = TermMatcher(COMMERCIAL_MARKERS, name="commercial")
REVIEW = TermMatcher(REVIEW_KEYWORDS, name="review")
JUNK = TermMatcher(JUNK_PATTERNS, name="junk")
RUSSIA = TermMatcher(RUSSIA_KEYWORDS, stem=False, name="russia")

# Технические маркеры: если они есть, скидка/обзор не отменяют пользу материала.
TECHNICAL_MARKERS = TermMatcher(
    [
        "протокол", "dpi", "vless", "v2ray", "xray", "wireguard", "openvpn",
        "shadowsocks", "trojan", "reality", "xhttp", "hysteria", "sing-box",
        "utls", "fragment", "сниффинг", "sniffing", "туннел", "маршрут",
        "шифрован", "tls", "sni", "dns", "обход", "блокиров", "заблокирован",
        "замедлен", "утечк", "уязвим", "эксплойт", "cve", "трафик",
    ],
    name="technical",
)

LISTICLE_MARKERS = TermMatcher(
    ["top 10", "top-10", "best", "лучшие", "подборка", "рейтинг", "топ-", "топ "],
    name="listicle",
)


def ai_relevance_score(text: str) -> int:
    """Ключевой скор по AI-тематике (совместимость с прежним поведением)."""
    return AI_STRONG.count(text) * 2 + AI_WEAK.count(text)


def block_relevance_score(text: str) -> int:
    return len(BLOCK.matches(text)) * 5


def is_russian_related(text: str) -> bool:
    return RUSSIA.any(text)


def profile_hits(text: str) -> List[str]:
    return PROFILE.matches(text)


def is_profile_topic(text: str) -> bool:
    return PROFILE.any(text)


def is_promo_content(text: str) -> bool:
    return len(PROMO.matches(text)) >= 2


def is_junk_content(text: str) -> bool:
    return JUNK.any(text)


def is_commercial_roundup(text: str) -> bool:
    """Рекламная подборка/скидочный пост, а не технический материал."""
    commercial = COMMERCIAL.matches(text)
    if not commercial:
        return False
    if TECHNICAL_MARKERS.any(text):
        return False
    if LISTICLE_MARKERS.any(text) or REVIEW.any(text):
        return True
    # «Скидка 50% на коммерческий VPN» — просто предложение, без техники
    return True


def evaluate_relevance(article: Article) -> Tuple[bool, str]:
    """Пропускать ли статью дальше. Возвращает (ok, причина_отказа)."""
    text = article.text
    lowered = text.lower()

    age_hours = article.age_hours()
    if age_hours > config.max_article_age_hours:
        return False, f"TOO_OLD ({age_hours:.0f}h)"

    games = GAMES.matches(lowered)
    if games:
        return False, f"GAME ({games[0]})"

    business = BUSINESS.matches(lowered)
    if business:
        return False, f"BUSINESS ({business[0]})"

    junk = JUNK.matches(lowered)
    if junk:
        return False, f"JUNK_VACANCY ({junk[0]})"

    if is_promo_content(lowered):
        return False, "PROMO"

    has_profile = is_profile_topic(lowered)
    review_hits = REVIEW.matches(lowered)

    if review_hits and not has_profile:
        # «Обзор» и «тест» вне профильной темы — почти всегда реклама или листикл
        if is_commercial_roundup(lowered) or LISTICLE_MARKERS.any(lowered):
            return False, f"COMMERCIAL_ROUNDUP ({review_hits[0]})"

    if is_commercial_roundup(lowered):
        return False, "COMMERCIAL_OFFER"

    has_strong_ai = AI_STRONG.any(lowered)
    has_weak_ai = AI_WEAK.any(lowered)
    is_ai = has_strong_ai or (has_weak_ai and config.min_ai_score <= 1)

    if has_profile:
        return True, ""

    if is_ai:
        return True, ""

    return False, "NOT_AI_NOT_PROFILE"


def is_relevant(article: Article) -> bool:
    ok, reason = evaluate_relevance(article)
    if not ok:
        logger.info(f"  🚫 {reason}: {article.title[:55]}")
    else:
        logger.info(f"  ✅ PASS: {article.title[:55]}")
    return ok
