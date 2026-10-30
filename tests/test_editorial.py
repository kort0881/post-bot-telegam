import json

import pytest

from bot.assessment import Assessment
from bot.editorial import (
    build_post_text,
    build_prompt,
    check_style,
    choose_format,
    generate_post,
    normalize_formatting,
    parse_quality,
    strip_meta_leaks,
    validate_post,
)
from bot.models import Article, PostFormat, Topic
from bot.rating import ScoreBreakdown


def article(full_text=None):
    return Article(
        title="РКН расширил блокировку Cloudflare, часть VLESS-подключений перестала работать",
        summary="Провайдеры фильтруют трафик на уровне DPI",
        link="https://example.com/a",
        source="Роскомсвобода",
        full_text=full_text,
    )


def assessment(fmt=PostFormat.HOWTO, trigger="угроза"):
    breakdown = ScoreBreakdown(topic=25, usefulness=20, freshness=17, novelty=10,
                              discussion=8, reliability=5)
    return Assessment(
        breakdown=breakdown, post_format=fmt, trigger=trigger,
        worth_publishing=True, reason="ok",
        main_point="DPI-фильтрация ломает VLESS",
        facts=["часть VLESS-подключений не устанавливается", "провайдеры фильтруют трафик"],
    )


GOOD_BODY = (
    "VLESS-подключения перестали устанавливаться у части пользователей после того, "
    "как провайдеры включили фильтрацию на уровне DPI. Проблема не в клиенте и не в "
    "конфигурации сервера: соединение рвётся ещё до обмена данными, поэтому привычные "
    "перезапуски приложения ничего не меняют.\n\n"
    "Проверить, блокируется ли конкретный сервер или протокол целиком, можно по "
    "поведению рукопожатия: если TLS-сессия не устанавливается к любому адресу подсети, "
    "дело в фильтрации, а не в ключе. Роскомсвобода опубликовала инструкцию, какие "
    "параметры стоит изменить после обновления: домен маскировки, порт и SNI.\n\n"
    "Ограничение: часть операторов применяет дополнительные механизмы обнаружения "
    "трафика, и универсального обхода для них пока нет. Если после смены SNI "
    "соединение не восстанавливается, проблема, вероятно, на стороне оператора."
)


class RouteLLM:
    """Отвечает по-разному: на промпт генерации — текстом, на самопроверку — JSON."""

    def __init__(self, bodies=None, quality=None):
        self.bodies = list(bodies or [GOOD_BODY])
        self.quality = quality or {"first_line": 8, "intrigue": 8, "read_to_end": 7,
                                   "value": 8, "share_potential": 7}
        self.calls = []

    async def complete(self, prompt, **kwargs):
        self.calls.append(prompt)
        if "Оцени готовую публикацию" in prompt:
            return json.dumps(self.quality, ensure_ascii=False)
        return self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]


def test_prompt_contains_structure_trigger_and_length():
    prompt = build_prompt(article(), assessment(), PostFormat.HOWTO)
    assert "Практическое решение" in prompt
    assert "Триггер подачи: угроза" in prompt
    assert "700–1500" in prompt
    assert "не заканчивай пост шаблонным вопросом" in prompt.lower()
    assert "DPI-фильтрация ломает VLESS" in prompt


def test_prompt_warns_when_full_text_missing():
    prompt = build_prompt(article(full_text=None), assessment(), PostFormat.HOWTO)
    assert "полный текст статьи недоступен" in prompt
    prompt_with_text = build_prompt(article(full_text="x" * 500), assessment(), PostFormat.HOWTO)
    assert "полный текст статьи недоступен" not in prompt_with_text


def test_validate_post_accepts_good_howto():
    ok, reason = validate_post(GOOD_BODY, PostFormat.HOWTO)
    assert ok, reason


@pytest.mark.parametrize("bad", [
    "В современном мире VPN стал необходимостью. " + GOOD_BODY,
    GOOD_BODY + "\n\nСрочно! Все должны это знать.",
    GOOD_BODY + "\n\nА что вы думаете?",
])
def test_validate_post_rejects_cliches_and_clickbait(bad):
    ok, reason = validate_post(bad, PostFormat.HOWTO)
    assert not ok
    assert reason.split(" ")[0] in {
        "BANNED_OPENING", "CLICKBAIT", "BANNED_ENDING", "ENDS_WITH_QUESTION",
    }


def test_validate_post_rejects_short_text_for_format():
    ok, reason = validate_post("Короткий пост про DPI. Ещё одно предложение.", PostFormat.ANALYSIS)
    assert not ok
    assert reason.startswith("TEXT_TOO_SHORT")


def test_normalize_formatting_converts_markdown_bold():
    raw = "**Что делать**\n\n1. Скачайте клиент\n2. Запустите"
    out = normalize_formatting(raw)
    assert "**" not in out
    assert "<b>Что делать</b>" in out


def test_strip_meta_leaks_removes_editor_notes():
    raw = ("Текст про обход блокировок, вполне осмысленный. "
           "Ссылка на источник будет добавлена системой. "
           "Ещё одно предложение по существу.")
    out = strip_meta_leaks(raw)
    assert "будет добавлена системой" not in out.lower()
    assert "обход блокировок" in out


def test_validate_post_rejects_fake_test_claim():
    bad = GOOD_BODY.replace("Проверить, блокируется ли", "Мы проверили, блокируется ли")
    ok, reason = validate_post(bad, PostFormat.HOWTO)
    assert not ok
    assert reason.startswith("FAKE_TEST_CLAIM")


def test_quality_gate_requires_value_and_read_to_end():
    from bot.editorial import QualityReport

    empty = QualityReport(first_line=8, intrigue=8, read_to_end=0, value=0, share_potential=0)
    assert empty.passed is False

    good = QualityReport(first_line=8, intrigue=8, read_to_end=7, value=8, share_potential=7)
    assert good.passed is True


def test_check_style_flags_question_ending():
    ok, reason = check_style("Текст про блокировки. Работает ли это у вас?")
    assert not ok
    assert reason == "ENDS_WITH_QUESTION"


def test_choose_format_respects_row_limit():
    proposed = PostFormat.HOWTO
    chosen = choose_format(assessment(PostFormat.HOWTO), [PostFormat.HOWTO, PostFormat.HOWTO])
    assert chosen != proposed


def test_choose_format_keeps_material_format_normally():
    assert choose_format(assessment(PostFormat.ANALYSIS), [PostFormat.HOWTO]) == PostFormat.ANALYSIS


def test_build_post_text_has_no_disclaimer_and_short_hashtags():
    text = build_post_text(article(), GOOD_BODY, PostFormat.HOWTO, Topic.BYPASS)
    assert "Минюста" not in text       # дисклеймер убран (ТЗ §8)
    assert "⚠️" not in text
    assert "🔗 <a href=\"https://example.com/a\">Источник</a>" in text
    hashtags = text.split("\n\n")[-2]
    assert 1 <= len(hashtags.split()) <= 3


@pytest.mark.asyncio
async def test_generate_post_returns_valid_text():
    llm = RouteLLM()
    generated = await generate_post(article(), assessment(), PostFormat.HOWTO, llm)
    assert generated is not None
    assert generated.post_format == PostFormat.HOWTO
    assert generated.quality.source == "llm"
    assert generated.attempts == 1


@pytest.mark.asyncio
async def test_generate_post_retries_when_quality_is_low():
    low = {"first_line": 3, "intrigue": 4, "read_to_end": 5, "value": 5, "share_potential": 4}
    llm = RouteLLM(bodies=[GOOD_BODY, GOOD_BODY], quality=low)
    generated = await generate_post(article(), assessment(), PostFormat.HOWTO, llm)
    assert generated is None  # обе попытки не прошли самопроверку
    gen_prompts = [p for p in llm.calls if not p.startswith("Оцени готовую публикацию")]
    assert len(gen_prompts) == 2
    assert "Прошлый вариант отклонён" in gen_prompts[1]


@pytest.mark.asyncio
async def test_generate_post_retries_after_banned_opening():
    bad = "В современном мире всё изменилось. " + GOOD_BODY
    llm = RouteLLM(bodies=[bad, GOOD_BODY])
    generated = await generate_post(article(), assessment(), PostFormat.HOWTO, llm)
    assert generated is not None
    assert generated.attempts == 2


def test_parse_quality_tolerates_fences():
    raw = "```json\n{\"first_line\": 9, \"intrigue\": 8}\n```"
    report = parse_quality(raw)
    assert report.first_line == 9
    assert report.source == "llm"
