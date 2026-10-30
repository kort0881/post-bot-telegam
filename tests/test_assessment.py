import json

import pytest

from bot.assessment import (
    assess_article,
    build_breakdown,
    parse_assessment,
)
from bot.llm import LLMError
from bot.models import Article, PostFormat


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.prompts = []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.payload


def article():
    return Article(
        title="РКН расширил блокировку Cloudflare, часть VLESS-подключений перестала работать",
        summary="Провайдеры фильтруют трафик на уровне DPI",
        link="https://example.com/a",
        source="Роскомсвобода",
    )


GOOD = {
    "topic_relevance": 9, "usefulness": 8, "novelty": 7, "discussion": 8,
    "format": "howto", "trigger": "угроза", "worth_publishing": True,
    "reason": "практическая проблема у пользователей",
    "main_point": "DPI-фильтрация ломает VLESS",
    "facts": ["часть подключений не устанавливается", "провайдеры фильтруют трафик"],
}


@pytest.mark.asyncio
async def test_llm_assessment_maps_scores():
    llm = FakeLLM(json.dumps(GOOD, ensure_ascii=False))
    result = await assess_article(article(), llm)
    assert result.source == "llm"
    assert result.post_format == PostFormat.HOWTO
    assert result.trigger == "угроза"
    assert result.worth_publishing is True
    breakdown = result.breakdown.as_dict()
    assert breakdown["topic"] == 23  # 9 * 2.5 с округлением
    assert breakdown["usefulness"] == 20
    assert breakdown["novelty"] == 11  # 7 * 1.5
    assert breakdown["discussion"] == 8
    assert 0 < breakdown["total"] <= 100


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_heuristics():
    llm = FakeLLM(error=LLMError("все модели отказали"))
    result = await assess_article(article(), llm)
    assert result.source == "heuristic"
    assert result.breakdown.total > 0
    assert "ЭВРИСТИКА" in result.breakdown.reasons[0]


@pytest.mark.asyncio
async def test_invalid_json_falls_back():
    llm = FakeLLM("извините, не могу помочь")
    result = await assess_article(article(), llm)
    assert result.source == "heuristic"


def test_parse_assessment_accepts_russian_format_names():
    data = parse_assessment(json.dumps(dict(GOOD, format="Практическое решение"), ensure_ascii=False))
    assert data["format"] == "howto"


def test_parse_assessment_unknown_format_keeps_scores():
    data = parse_assessment(json.dumps(dict(GOOD, format="memes"), ensure_ascii=False))
    assert data is not None
    assert data["format"] == ""      # формат доберём эвристикой
    assert data["topic_relevance"] == 9


def test_parse_assessment_tolerates_code_fence():
    wrapped = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    assert parse_assessment(wrapped)["format"] == "howto"


def test_parse_assessment_clamps_out_of_range():
    data = parse_assessment(json.dumps(dict(GOOD, topic_relevance=99, usefulness=-5)))
    assert data["topic_relevance"] == 10
    assert data["usefulness"] == 0


TRUNCATED = ('{\n  "topic_relevance": 6,\n  "usefulness": 4,\n  "novelty": 7,\n'
             '  "discussion": 6,\n  "format": "tool",\n  "trigger": "выгода",\n'
             '  "worth_publishing": true,\n  "reason": "Новость о стартапе с $17 млн'
             ' инвестиций, разрабатывающем AI-модель для автоматизированного проектирования,'
             ' интересна аудитории, с')


def test_truncated_json_is_repaired_not_dropped():
    """Живой ответ модели обрывается по max_tokens — оценка не должна теряться."""
    data = parse_assessment(TRUNCATED)
    assert data is not None
    assert data["topic_relevance"] == 6
    assert data["format"] == "tool"
    assert data["trigger"] == "выгода"
    assert data["worth_publishing"] is True


@pytest.mark.asyncio
async def test_truncated_json_keeps_llm_scores():
    llm = FakeLLM(TRUNCATED)
    result = await assess_article(article(), llm)
    assert result.source == "llm"
    assert result.breakdown.topic == 15  # 6 * 2.5


def test_json_with_missing_format_falls_back_to_heuristics():
    partial = '{"topic_relevance": 8, "usefulness": 7, "novelty": 6, "discussion": 5'
    data = parse_assessment(partial)
    assert data is not None
    assert data["format"] == ""


@pytest.mark.asyncio
async def test_offtopic_material_is_not_worth_publishing():
    """Nvidia/шпионаж вне профиля канала не должен публиковаться даже при интересе."""
    off = Article(
        title="Китайские шпионы следили за главой Nvidia, утверждает NYT",
        summary="Расследование о поставках чипов",
        link="https://example.com/nvidia",
        source="The Decoder",
    )
    payload = dict(GOOD, topic_relevance=7, worth_publishing=True, format="consequence")
    llm = FakeLLM(json.dumps(payload, ensure_ascii=False))
    result = await assess_article(off, llm)
    assert result.worth_publishing is False


@pytest.mark.asyncio
async def test_profile_material_with_same_score_is_worth_publishing():
    payload = dict(GOOD, topic_relevance=7, worth_publishing=True, format="consequence")
    llm = FakeLLM(json.dumps(payload, ensure_ascii=False))
    result = await assess_article(article(), llm)
    assert result.worth_publishing is True


def test_build_breakdown_keeps_weights():
    breakdown = build_breakdown(article(), {
        "topic_relevance": 10, "usefulness": 10, "novelty": 10, "discussion": 10,
    })
    assert breakdown.topic == 25
    assert breakdown.usefulness == 25
    assert breakdown.novelty == 15
    assert breakdown.discussion == 10
    assert breakdown.total <= 100
