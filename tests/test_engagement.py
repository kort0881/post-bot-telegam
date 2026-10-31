"""Тесты вовлечения (ТЗ §6): рубрики, механики, опросы."""
from datetime import datetime, timedelta, timezone

from bot import engagement
from bot.config import config
from bot.models import Article, PostFormat


def make_article(title: str, summary: str = "") -> Article:
    return Article(title=title, summary=summary, link="https://example.com/a", source="Habr AI")


def _post(rubric: str, days_ago: float) -> dict:
    date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"rubric": rubric, "date": date.strftime("%Y-%m-%d %H:%M:%S")}


def test_choose_rubric_for_format():
    plan = engagement.choose_rubric(PostFormat.HOWTO, [])
    assert plan is not None
    assert plan.name == "Что делать"
    assert plan.header.startswith("🧰")


def test_rubric_not_repeated_in_a_row():
    posts = [_post("Что делать", 5), _post("", 6), _post("", 7)]
    assert engagement.choose_rubric(PostFormat.HOWTO, posts) is None


def test_rubric_respects_cooldown(monkeypatch):
    monkeypatch.setattr(config, "rubric_cooldown_days", 3)
    posts = [_post("", 0.2), _post("", 0.5), _post("Что делать", 1)]
    assert engagement.choose_rubric(PostFormat.HOWTO, posts) is None

    old = [_post("", 0.2), _post("", 0.5), _post("Что делать", 10)]
    assert engagement.choose_rubric(PostFormat.HOWTO, old) is not None


def test_engagement_hypothesis_for_broken_connection():
    article = make_article("После обновления VLESS перестал подключаться у части провайдеров")
    request = engagement.engagement_request(article, PostFormat.HOWTO)
    assert request is not None
    assert request.kind == "hypothesis"
    assert "проверяемую просьбу" in request.instruction or "проверить" in request.instruction


def test_engagement_cases_for_mass_complaints():
    article = make_article("Массовые жалобы пользователей на обход DPI у провайдера",
                           "Пользователи сообщают о сбоях в регионах")
    request = engagement.engagement_request(article, PostFormat.CONSEQUENCE)
    assert request is not None
    assert request.kind == "cases"


def test_engagement_continuation_for_popular_topic():
    article = make_article("Как работает механизм DPI и обход через VLESS")
    request = engagement.engagement_request(article, PostFormat.ANALYSIS, ["bypass"])
    assert request is not None
    assert request.kind == "continue"


def test_engagement_none_for_plain_news():
    article = make_article("Google выпустила новую модель Gemini", "Компания обновила линейку")
    assert engagement.engagement_request(article, PostFormat.TOOL) is None


def test_poll_templates_by_context():
    updated = make_article("Обновление клиента Karing: что изменилось")
    spec = engagement.build_poll(updated, PostFormat.CONSEQUENCE,
                                 {"polls_today": 0, "posts_since_last_poll": 10})
    assert spec is not None
    assert spec.question == engagement.POLL_TEMPLATES["recovered"][0]
    assert len(spec.options) == 4


def test_poll_limits_block_spam():
    article = make_article("Проверка протокола VLESS после блокировки")
    assert engagement.build_poll(article, PostFormat.HOWTO,
                                 {"polls_today": 5, "posts_since_last_poll": 10}) is None
    assert engagement.build_poll(article, PostFormat.HOWTO,
                                 {"polls_today": 0, "posts_since_last_poll": 1}) is None


def test_poll_disabled_by_config(monkeypatch):
    monkeypatch.setattr(config, "polls_enabled", False)
    article = make_article("Проверка протокола VLESS после блокировки")
    assert engagement.build_poll(article, PostFormat.HOWTO,
                                 {"polls_today": 0, "posts_since_last_poll": 10}) is None


def test_poll_none_for_unrelated_topic():
    article = make_article("OpenAI показала новую модель для генерации картинок")
    assert engagement.build_poll(article, PostFormat.TOOL,
                                 {"polls_today": 0, "posts_since_last_poll": 10}) is None
