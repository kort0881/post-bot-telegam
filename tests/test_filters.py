from datetime import datetime, timedelta, timezone

import pytest

from bot.filters import evaluate_relevance, is_junk_content, is_relevant
from bot.models import Article


def make(title, summary="", source="3DNews Software", age_hours=2, link=None):
    return Article(
        title=title,
        summary=summary,
        link=link or f"https://example.com/{abs(hash(title))}",
        source=source,
        published=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


# --- §2.1 слово «работа» больше не отсеивает полезное ---
@pytest.mark.parametrize("title", [
    "Новый VPN работает даже при блокировках",
    "Сервис заработал после обновления",
    "Как работает новый протокол обхода DPI",
    "Работоспособность обхода блокировок проверили эксперты",
])
def test_rabota_ne_junk(title):
    # слово «работа» больше не считается признаком вакансии
    assert is_junk_content(title.lower()) is False


@pytest.mark.parametrize("title", [
    "Новый VPN работает даже при блокировках",
    "Как работает новый протокол обхода DPI",
    "Работоспособность обхода блокировок проверили эксперты",
])
def test_profile_news_about_rabota_pass_relevance(title):
    ok, reason = evaluate_relevance(make(title))
    assert ok, reason


def test_formy_vakansii_ostayutsya_musorom():
    for title in [
        "Открыта вакансия менеджера по продажам",
        "Ищем сотрудника в отдел разработки",
        "Требуется специалист по информационной безопасности",
        "Присылайте резюме на почту",
    ]:
        assert is_junk_content(title.lower()) is True, title


# --- §2.2 обзоры и тесты профильных тем проходят ---
@pytest.mark.parametrize("title", [
    "Протестировали новый способ обхода блокировок DPI",
    "Тестируем новый VPN-протокол VLESS",
    "Обзор инструментов для обхода DPI",
])
def test_profile_reviews_pass(title):
    ok, reason = evaluate_relevance(make(title))
    assert ok, reason


@pytest.mark.parametrize("title", [
    "Топ-10 лучших VPN 2026 со скидкой",
    "Обзор: лучшие наушники года",
    "Скидка 50% на коммерческий VPN",
    "Промокод: подписка на VPN за 99 рублей",
])
def test_commercial_roundups_rejected(title):
    ok, reason = evaluate_relevance(make(title))
    assert not ok, f"должно отбрасываться: {title} ({reason})"


# --- тематические проверки сохраняются ---
def test_ai_news_passes():
    ok, reason = evaluate_relevance(make("OpenAI выпустила новую модель GPT-5"))
    assert ok, reason


def test_games_and_business_still_out():
    assert not evaluate_relevance(make("Обзор Baldur's Gate 3 на PS5"))[0]
    assert not evaluate_relevance(make("Новый CEO Anthropic уходит с поста"))[0]


def test_unrelated_news_rejected():
    ok, reason = evaluate_relevance(make("В Москве открыли новую станцию метро"))
    assert not ok
    assert reason == "NOT_AI_NOT_PROFILE"


def test_old_article_rejected():
    ok, reason = evaluate_relevance(make("Блокировка VPN в России", age_hours=1000))
    assert not ok
    assert reason.startswith("TOO_OLD")


def test_is_relevant_compat_api():
    assert is_relevant(make("РКН заблокировал новый протокол VLESS")) is True
