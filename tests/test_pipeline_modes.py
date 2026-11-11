"""Тесты режимов прогона (ТЗ §11) и связки «рубрика + вовлечение + опрос».

Ни сети, ни Groq, ни Telegram: модель и бот подменяются, фиды тоже.
"""
from types import SimpleNamespace

import pytest

from bot import engagement, pipeline
from bot.assessment import Assessment
from bot.config import config
from bot.db import PostedManager
from bot.editorial import QualityReport
from bot.models import Article, PostFormat
from bot.rating import ScoreBreakdown

BODY = " ".join([
    "VLESS перестал подключаться после обновления клиента у части пользователей.",
    "Причина обычно в подмене SNI или в устаревшем списке адресов на стороне клиента.",
    "Проверить это можно по логам: там видно, на каком шаге обрывается рукопожатие.",
    "Если соединение поднимается, но данные не идут, дело чаще всего в размере MTU.",
    "Второй шаг — сравнить поведение двух транспортов в одной и той же сети.",
    "Смена порта помогает не всегда: часть сетей режет трафик по шаблону пакетов.",
    "Профиль подключения стоит обновить вручную, если клиент потянул старые адреса.",
    "Настройки ядра трогать не нужно, достаточно изменить параметры самого клиента.",
    "Ограничение метода в том, что он не спасает при полной блокировке подсети.",
    "Итог: сначала диагностика по шагам, потом изменение профиля подключения.",
])


def make_article() -> Article:
    return Article(
        title="VLESS перестал подключаться после обновления у провайдера",
        summary="Пользователи сообщают о сбоях, провайдер блокирует соединение",
        link="https://example.com/vless",
        source="Habr AI",
    )


class FakeBot:
    def __init__(self):
        self.messages = []
        self.polls = []
        self.poll_calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return SimpleNamespace(message_id=901)

    async def send_poll(self, chat_id, question, options, **kwargs):
        self.polls.append((question, tuple(options)))
        self.poll_calls.append({
            "question": question,
            "options": tuple(options),
            "kwargs": dict(kwargs),
        })
        return SimpleNamespace(message_id=902)


def fake_assessment(article: Article) -> Assessment:
    breakdown = ScoreBreakdown()
    breakdown.topic, breakdown.usefulness = 25, 20
    breakdown.freshness, breakdown.novelty = 15, 10
    breakdown.discussion, breakdown.reliability = 8, 4
    return Assessment(breakdown=breakdown, post_format=PostFormat.HOWTO,
                      trigger="последствия", worth_publishing=True,
                      main_point=article.title, facts=["факт из источника"])


@pytest.fixture()
def runtime(monkeypatch):
    async def fake_feeds():
        return [make_article()]

    async def fake_assess(article, llm=None):
        return fake_assessment(article)

    async def fake_generate(article, assessment, fmt, llm=None, extra_guidance=None):
        return SimpleNamespace(body=BODY, post_format=fmt,
                               quality=QualityReport(first_line=8, intrigue=8, read_to_end=8,
                                                     value=8, share_potential=7))

    monkeypatch.setattr(pipeline, "load_all_feeds", fake_feeds)
    monkeypatch.setattr(pipeline, "assess_article", fake_assess)
    monkeypatch.setattr(pipeline, "generate_post", fake_generate)
    bot = FakeBot()
    return SimpleNamespace(bot=bot, llm=None, init=lambda: None)


async def test_collect_mode_only_fills_queue(db_file, runtime, monkeypatch):
    monkeypatch.setattr(config, "channel_id", "@test_channel")
    posted = PostedManager(db_file)
    posted.set_posting_mode("collect")

    result = await pipeline.run_cycle(runtime, posted)
    assert result["mode"] == "collect"
    assert result["reason"] == "collected"
    assert posted.queue_count() == 1
    assert runtime.bot.messages == []


async def test_approval_mode_waits_for_admin(db_file, runtime, monkeypatch):
    monkeypatch.setattr(config, "channel_id", "@test_channel")
    monkeypatch.setattr(config, "admin_chat_id", "")
    posted = PostedManager(db_file)
    posted.set_posting_mode("approval")

    result = await pipeline.run_cycle(runtime, posted)
    assert result["reason"] == "waiting_approval"
    assert result["queue_id"] is not None
    assert runtime.bot.messages == []
    assert posted.queue_count() == 1
    assert posted.get_stats()["total_posted"] == 0


async def test_auto_mode_publishes_with_rubric_poll_and_stats(db_file, runtime, monkeypatch):
    monkeypatch.setattr(config, "channel_id", "@test_channel")
    monkeypatch.setattr(config, "polls_enabled", True)
    monkeypatch.setattr(config, "quizzes_enabled", False)
    posted = PostedManager(db_file)
    posted.set_posting_mode("auto")

    result = await pipeline.run_cycle(runtime, posted)
    assert result["published"] is True
    assert len(runtime.bot.messages) == 1
    # рубрика формата howto добавляется первой строкой
    assert runtime.bot.messages[0].startswith("🧰 Что делать")

    rows = posted.stats_rows(1)
    assert len(rows) == 1
    assert rows[0]["message_id"] == 901
    assert rows[0]["rubric"] == "Что делать"

    # опрос привязан к теме: сначала главная задача — как пользуются нашим ботом,
    # формулировки про обновление, которого канал не делал, больше нет
    assert len(runtime.bot.polls) == 1
    question, options = runtime.bot.polls[0]
    assert "наш" in question.lower()
    assert "обновлен" not in question.lower()
    assert len(options) == 4
    assert posted.poll_state()["recent_keys"]


async def test_auto_mode_sends_quiz_as_telegram_quiz(db_file, runtime, monkeypatch):
    import json

    monkeypatch.setattr(config, "channel_id", "@test_channel")
    monkeypatch.setattr(config, "polls_enabled", True)
    monkeypatch.setattr(config, "quizzes_enabled", True)
    monkeypatch.setattr(config, "quiz_every_days", 5)
    posted = PostedManager(db_file)
    posted.set_posting_mode("auto")
    # вопросы про наш бот и про настроения уже выходили — доходит очередь до викторины
    done = {key: "2026-11-10 08:00:00"
            for key in list(engagement.SERVICE_POLL_KEYS) + list(engagement.MOOD_POLL_KEYS)}
    posted.set_setting("poll_recent_keys", json.dumps(done))

    result = await pipeline.run_cycle(runtime, posted)
    assert result["published"] is True
    assert len(runtime.bot.poll_calls) == 1
    call = runtime.bot.poll_calls[0]
    assert call["kwargs"].get("type") == "quiz"
    assert isinstance(call["kwargs"].get("correct_option_id"), int)
    assert call["kwargs"].get("explanation")
    assert "allows_multiple_answers" not in call["kwargs"]
    assert posted.poll_state()["quiz_last_at"]


async def test_engagement_guidance_reaches_generation(db_file, runtime, monkeypatch):
    monkeypatch.setattr(config, "channel_id", "@test_channel")
    seen = {}

    async def spy_generate(article, assessment, fmt, llm=None, extra_guidance=None):
        seen["guidance"] = extra_guidance
        return SimpleNamespace(body=BODY, post_format=fmt,
                               quality=QualityReport(first_line=8, intrigue=8, read_to_end=8,
                                                     value=8, share_potential=7))

    monkeypatch.setattr(pipeline, "generate_post", spy_generate)
    posted = PostedManager(db_file)
    posted.set_posting_mode("auto")

    await pipeline.run_cycle(runtime, posted)
    assert seen["guidance"], "вовлечение должно передаваться в генерацию"
    assert "провер" in seen["guidance"].lower() or "просьб" in seen["guidance"].lower()
