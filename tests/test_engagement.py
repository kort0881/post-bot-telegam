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


def _state(**overrides) -> dict:
    base = {"polls_today": 0, "posts_since_last_poll": 10,
            "recent_keys": {}, "quiz_last_at": ""}
    base.update(overrides)
    return base


def _ts(days_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%d %H:%M:%S")


VPN_KEYS = ("connection", "client_problem", "protocol", "client_choice")
AI_KEYS = ("llm_tools", "ai_pay", "local_ai", "ai_work")


def test_poll_follows_topic_vpn(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    article = make_article("VLESS перестал подключаться после обновления у провайдера")
    spec = engagement.build_poll(article, PostFormat.CONSEQUENCE, _state())
    assert spec is not None
    assert spec.key == "connection"
    # формулировка не должна намекать на обновление, которого канал не делал
    assert "обновлен" not in spec.question.lower()
    assert len(spec.options) == 4


def test_poll_generic_update_does_not_trigger_vpn_question(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    article = make_article("Масштабное обновление продуктов Adobe: новые функции")
    spec = engagement.build_poll(article, PostFormat.TOOL, _state())
    assert spec is not None
    assert spec.key not in VPN_KEYS


def test_poll_ai_topic_gets_ai_question(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    article = make_article("Google открыла доступ к своей самой мощной модели")
    spec = engagement.build_poll(article, PostFormat.TOOL, _state())
    assert spec is not None
    assert spec.key in AI_KEYS


def test_poll_does_not_repeat_same_key(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    monkeypatch.setattr(config, "poll_reuse_days", 14)
    article = make_article("VLESS перестал подключаться после обновления у провайдера")
    state = _state(recent_keys={"connection": _ts(1)})
    spec = engagement.build_poll(article, PostFormat.CONSEQUENCE, state)
    assert spec is not None
    assert spec.key != "connection"


def test_poll_silent_when_all_keys_on_cooldown(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    article = make_article("Microsoft показала новую нейросеть для офисных задач")
    state = _state(recent_keys={key: _ts(1) for key in AI_KEYS})
    assert engagement.build_poll(article, PostFormat.TOOL, state) is None


def test_quiz_sent_for_topic(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", True)
    monkeypatch.setattr(config, "quiz_every_days", 5)
    article = make_article("OpenAI показала новую модель GPT для работы с компьютером")
    spec = engagement.build_poll(article, PostFormat.TOOL, _state())
    assert spec is not None
    assert spec.kind == "quiz"
    assert spec.key.startswith("ai_")
    assert spec.correct_option_id is not None
    assert 0 <= spec.correct_option_id < len(spec.options)
    assert spec.explanation


def test_quiz_respects_frequency(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", True)
    monkeypatch.setattr(config, "quiz_every_days", 5)
    article = make_article("OpenAI показала новую модель GPT для работы с компьютером")
    state = _state(quiz_last_at=_ts(1))
    spec = engagement.build_poll(article, PostFormat.TOOL, state)
    assert spec is not None
    assert spec.kind == "poll"


def test_quiz_key_not_repeated(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", True)
    monkeypatch.setattr(config, "quiz_reuse_days", 30)
    article = make_article("VLESS перестал подключаться после блокировки у провайдера")
    first = engagement.build_poll(article, PostFormat.CONSEQUENCE, _state())
    assert first is not None and first.kind == "quiz"
    state = _state(recent_keys={first.key: _ts(1)})
    second = engagement.build_poll(article, PostFormat.CONSEQUENCE, state)
    assert second is not None
    assert second.key != first.key


def test_quiz_bank_is_valid():
    for key, template in engagement.QUIZ_BANK.items():
        assert 2 <= len(template.options) <= 10, key
        assert 0 <= template.correct < len(template.options), key
        assert len(template.question) <= 300, key
        assert all(len(option) <= 100 for option in template.options), key
        assert template.explanation and len(template.explanation) <= 200, key


def test_poll_limits_block_spam(monkeypatch):
    monkeypatch.setattr(config, "quizzes_enabled", False)
    article = make_article("Проверка протокола VLESS после блокировки")
    assert engagement.build_poll(article, PostFormat.HOWTO,
                                 _state(polls_today=5)) is None
    assert engagement.build_poll(article, PostFormat.HOWTO,
                                 _state(posts_since_last_poll=1)) is None


def test_poll_disabled_by_config(monkeypatch):
    monkeypatch.setattr(config, "polls_enabled", False)
    article = make_article("Проверка протокола VLESS после блокировки")
    assert engagement.build_poll(article, PostFormat.HOWTO, _state()) is None


def test_db_remembers_poll_keys(db_file):
    from bot.db import PostedManager

    posted = PostedManager(db_file)
    posted.note_poll_sent("connection", "poll")
    state = posted.poll_state()
    assert "connection" in state["recent_keys"]
    assert state["quiz_last_at"] == ""

    posted.note_poll_sent("ai_rag", "quiz")
    state = posted.poll_state()
    assert "ai_rag" in state["recent_keys"]
    assert state["quiz_last_at"]

    # повтор того же вопроса внутри кулдауна отсекается на уровне выбора
    state = dict(state)
    state.update({"polls_today": 0, "posts_since_last_poll": 10})
    article = make_article("VLESS перестал подключаться после обновления у провайдера")
    spec = engagement.build_poll(article, PostFormat.CONSEQUENCE, state)
    assert spec is not None
    assert spec.key != "connection"

