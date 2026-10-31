"""Тесты очереди и команд админа (ТЗ §11) без сети и без Telegram."""
from types import SimpleNamespace

from bot import admin
from bot.assessment import heuristic_assessment
from bot.config import config
from bot.db import PostedManager
from bot.editorial import QualityReport
from bot.models import Article

PHRASES = [
    "Первое, что бросается в глаза — изменились параметры маршрутизации на стороне клиента.",
    "Протокол перестаёт отвечать, если провайдер подменяет SNI в первых пакетах.",
    "Проверить это можно по логам: там видно, на каком шаге обрывается рукопожатие.",
    "Вторая причина — устаревший список адресов, который не обновился после перезапуска.",
    "Если соединение поднимается, но данные не идут, дело обычно в размере MTU.",
    "Смена порта помогает не всегда: часть сетей режет трафик по шаблону.",
    "Резервный вариант — переключиться на другой транспорт и сравнить поведение.",
    "Ограничение в том, что метод не спасает при полной блокировке подсети.",
    "Настройки ядра трогать не нужно, достаточно изменить профиль подключения.",
    "Проверять стоит по шагам, фиксируя результат на каждом этапе.",
    "Документация проекта описывает эти параметры, но примеры устарели.",
    "Итог: сначала диагностика, потом изменение конфигурации, а не наоборот.",
]


def long_body() -> str:
    return " ".join(PHRASES)


def make_article(title: str, link: str = "https://example.com/a") -> Article:
    return Article(title=title, summary="Краткое описание материала",
                   link=link, source="Habr AI")


def test_enqueue_dedupes_and_orders_by_score(db_file):
    posted = PostedManager(db_file)
    first = posted.enqueue_item(make_article("Первый материал"), "bypass", "howto", 70, "Что делать")
    second = posted.enqueue_item(make_article("Второй материал", "https://example.com/b"),
                                 "bypass", "analysis", 88, "")
    assert first and second

    again = posted.enqueue_item(make_article("Первый материал"), "bypass", "howto", 70, "Что делать")
    assert again == first
    assert posted.queue_count() == 2

    items = posted.queue_items()
    assert [item["id"] for item in items] == [second, first]
    assert items[0]["payload"]["title"] == "Второй материал"


def test_queue_decisions_and_posting_mode(db_file):
    posted = PostedManager(db_file)
    item_id = posted.enqueue_item(make_article("Материал"), "bypass", "howto", 75, "")

    assert posted.get_posting_mode() == config.posting_mode
    assert posted.set_posting_mode("approval") is True
    assert posted.get_posting_mode() == "approval"
    assert posted.set_posting_mode("чепуха") is False
    assert posted.get_posting_mode() == "approval"

    assert posted.decide_queue_item(item_id, "skipped", "не тема")
    assert posted.queue_item(item_id)["status"] == "skipped"
    assert posted.queue_count("pending") == 0


def test_format_queue_and_stats_text(db_file):
    posted = PostedManager(db_file)
    assert "пуста" in admin.format_queue([])

    posted.enqueue_item(make_article("Новый VLESS перестал подключаться"), "bypass", "howto", 81, "")
    text = admin.format_queue(posted.queue_items())
    assert "#1" in text and "81/100" in text and "howto" in text

    stats = admin.format_stats(posted)
    assert "Очередь: 1 ждут" in stats
    settings = admin.format_settings(posted)
    assert "Режим публикации" in settings


def test_is_admin_requires_id(monkeypatch):
    monkeypatch.setattr(config, "admin_ids", {42, 7})
    assert admin.is_admin(42) is True
    assert admin.is_admin(99) is False
    assert admin.is_admin(None) is False


def test_article_from_queue_item_roundtrip(db_file):
    posted = PostedManager(db_file)
    item_id = posted.enqueue_item(make_article("Возврат статьи"), "bypass", "howto", 70, "")
    item = posted.queue_item(item_id)
    article = admin.article_from_queue_item(item)
    assert article.title == "Возврат статьи"
    assert article.link == "https://example.com/a"
    assert article.source == "Habr AI"


async def test_publish_item_sends_once_and_records_stats(monkeypatch, db_file):
    posted = PostedManager(db_file)
    item_id = posted.enqueue_item(make_article("Публикация из очереди"), "bypass", "howto", 82,
                                  "Что делать")

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append(text)
            return SimpleNamespace(message_id=555)

    async def fake_assess(article, llm=None):
        return heuristic_assessment(article)

    async def fake_generate(article, assessment, fmt, llm=None, extra_guidance=None):
        return SimpleNamespace(body=long_body(), post_format=fmt,
                               quality=QualityReport(first_line=8, intrigue=8, read_to_end=8,
                                                     value=8, share_potential=7))

    monkeypatch.setattr(admin, "assess_article", fake_assess)
    monkeypatch.setattr(admin, "generate_post", fake_generate)

    runtime = SimpleNamespace(llm=None, bot=FakeBot(), init=lambda: None)
    ok, message = await admin.publish_item(item_id, posted, runtime, runtime.bot)
    assert ok is True, message
    assert len(sent) == 1
    assert "Что делать" in sent[0]

    stored = posted.queue_item(item_id)
    assert stored["status"] == "published"
    assert stored["message_id"] == 555

    rows = posted.stats_rows(1)
    assert len(rows) == 1
    assert rows[0]["message_id"] == 555
    assert rows[0]["post_format"] == "howto"
    # метрики ещё не собраны — неизвестны, а не нулевые
    assert rows[0]["views_1h"] is None


async def test_publish_item_rejects_processed_item(db_file):
    posted = PostedManager(db_file)
    item_id = posted.enqueue_item(make_article("Материал"), "bypass", "howto", 70, "")
    posted.decide_queue_item(item_id, "skipped", "тест")
    runtime = SimpleNamespace(llm=None, bot=None, init=lambda: None)
    ok, message = await admin.publish_item(item_id, posted, runtime, runtime.bot)
    assert ok is False
    assert "skipped" in message


def test_skip_item(db_file):
    posted = PostedManager(db_file)
    item_id = posted.enqueue_item(make_article("Лишний материал"), "bypass", "howto", 60, "")
    assert "убран" in admin.skip_item(item_id, posted)
    assert posted.queue_item(item_id)["status"] == "skipped"
    assert "нет" in admin.skip_item(9999, posted)
