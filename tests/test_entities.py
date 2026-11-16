"""Субъекты: извлечение и кулдаун на компанию/человека."""
import json

import pytest

from bot.assessment import assess_article, build_recent_block
from bot.config import config
from bot.db import PostedManager
from bot.entities import extract_entities
from bot.models import Article

# ===== извлечение =====

def test_cyrillic_declension_hits():
    # именно этот заголовок выходил в канале 16.11.2026
    assert extract_entities("Открытый ИИ потерял главного союзника: уход Хуанга") == ["nvidia"]


def test_latin_and_cyrillic_aliases_merge():
    assert extract_entities("У Anthropic Claude произошёл глобальный сбой") == ["anthropic"]
    assert extract_entities("Клод научился писать операционные системы") == ["anthropic"]


def test_multiple_entities_in_order():
    assert extract_entities("OpenAI и Anthropic судятся с Nvidia") == ["openai", "anthropic", "nvidia"]


def test_no_false_positive_on_common_words():
    assert extract_entities("Маска для лица защищает кожу") == []
    assert extract_entities("Рынок метизов вырос") == []
    assert extract_entities("") == []


def test_rkn_and_telegram_detected():
    assert extract_entities("РКН заблокировал Cloudflare") == ["rkn"]
    assert extract_entities("Роскомнадзор запустил фильтрацию трафика") == ["rkn"]
    assert extract_entities("Дуров рассказал о Telegram Premium") == ["telegram"]


# ===== кулдаун в БД =====

@pytest.fixture()
def manager(tmp_path):
    db_file = tmp_path / "posted.db"
    pm = PostedManager(str(db_file))
    yield pm
    pm.close()


def _article(title, link):
    return Article(title=title, summary="тест", link=link, source="3DNews Software")


def test_entity_stored_on_add(manager):
    manager.add(_article("Уход Хуанга из Nvidia", "https://example.com/1"), "hardware", "hardware")
    usage = manager.get_recent_entity_usage(72)
    assert "nvidia" in usage


def test_second_post_about_same_entity_blocked(manager):
    ok, _ = manager.check_entity_limit(["nvidia"])
    assert ok is True

    manager.add(_article("Уход Хуанга из Nvidia", "https://example.com/1"), "hardware", "hardware")

    ok, reason = manager.check_entity_limit(["nvidia"])
    assert ok is False
    assert reason.startswith("ENTITY_COOLDOWN")
    assert "nvidia" in reason


def test_other_entity_not_blocked(manager):
    manager.add(_article("Уход Хуанга из Nvidia", "https://example.com/1"), "hardware", "hardware")
    ok, reason = manager.check_entity_limit(["anthropic"])
    assert ok is True, reason


def test_core_entity_has_softer_limit(manager):
    # РКН — профиль канала: два материала за 48ч допустимы, третий нет
    assert "rkn" in config.core_entities
    manager.add(_article("РКН запустил фильтрацию", "https://example.com/2"), "block", "block")
    ok, reason = manager.check_entity_limit(["rkn"])
    assert ok is True, reason

    manager.add(_article("РКН вооружился ИИ", "https://example.com/3"), "block", "block")
    ok, reason = manager.check_entity_limit(["rkn"])
    assert ok is False
    assert "rkn" in reason


def test_legacy_rows_without_entities_are_counted(manager):
    # строки, записанные старым кодом (entities == []), тоже учитываются
    conn = manager._get_conn()
    conn.execute('''
        INSERT INTO posted_articles
        (url, norm_url, domain, title, title_normalized, title_word_signature,
         summary, entities, topic, subject, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ("https://ex.com/old", "ex.com/old", "ex.com", "Anthropic обновила Claude",
          "anthropic обновила claude", "sig", "", json.dumps([]), "llm", "llm", "The Decoder"))
    conn.commit()

    ok, reason = manager.check_entity_limit(["anthropic"])
    assert ok is False, reason


# ===== слой А: анализатор видит повторы =====

def test_recent_block_lists_titles():
    block = build_recent_block(["уход Хуанга", "Claude Fable 5.2"])
    assert "уход Хуанга" in block
    assert "Claude Fable 5.2" in block
    assert build_recent_block([]) == ""
    assert build_recent_block(None) == ""


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.payload


PAYLOAD = json.dumps({
    "topic_relevance": 9, "usefulness": 8, "novelty": 4, "discussion": 8,
    "format": "analysis", "trigger": "неожиданность", "worth_publishing": True,
    "repeats_recent": True, "reason": "тот же сюжет", "main_point": "сбой Claude",
    "facts": ["сбой затронул claude.ai"],
})


async def test_repeat_subject_blocks_publication():
    article = Article(
        title="У Anthropic Claude произошёл глобальный сбой",
        summary="второй сбой за неделю",
        link="https://example.com/claude",
        source="The Decoder",
    )
    llm = FakeLLM(PAYLOAD)
    result = await assess_article(article, llm, recent_titles=["Claude Fable 5.2 вышел"])
    assert result.repeats_recent is True
    assert result.worth_publishing is False
    assert "УЖЕ ВЫШЛО В КАНАЛЕ" in llm.prompts[0]


async def test_repeat_with_high_novelty_passes():
    payload = json.loads(PAYLOAD)
    payload["novelty"] = 9
    article = Article(
        title="Anthropic Claude научился запускать код в песочнице",
        summary="новая возможность",
        link="https://example.com/claude2",
        source="The Decoder",
    )
    result = await assess_article(article, FakeLLM(json.dumps(payload)))
    assert result.worth_publishing is True
