"""Генерация текста поста. Этап 1: логика прежняя, вынесена в модуль.

На этапе 2 промпты переписываются под пять редакционных форматов (ТЗ §5, §7),
а дисклеймер и лишние хештеги убираются (§8).
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from .config import BLOCK_KEYWORDS, config
from .llm import GroqLLM, LLMError
from .models import Article, Topic
from .textutils import (
    has_repeated_sentences,
    is_valid_post_text,
    strip_service_lines,
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "\n\n⚠️ Отдельные организации, упомянутые в данном материале, могут иметь статус "
    "«нежелательных» на территории РФ. Актуальный перечень размещён на официальном сайте "
    'Минюста РФ: <a href="https://minjust.gov.ru/ru/pages/perechen-inostrannyh-i-'
    'mezhdunarodnyh-organizacij-deyatelnost-kotoryh-priznana-nezhelatelnoj-na-territorii-'
    'rossiyskoy-federacii/">minjust.gov.ru</a>'
)

WATER_PHRASES = [
    "стоит отметить", "важно понимать", "интересно, что",
    "давайте разберёмся", "как мы знаем", "не секрет",
    "нельзя не отметить", "следует подчеркнуть",
    "почему это важно", "для чего это важно",
    "это важно потому что", "это меняет всё",
    "это открывает возможности", "это меняет правила",
    "может привести", "можно ожидать", "вероятно", "возможно",
    "отражает экспертизу", "укрепит позиции", "пользователи могут рассчитывать",
]

SUMMARY_PREFIXES = ("ПОСТ:", "НОВОСТЬ:", "Заголовок:", "Содержание:", "Источник:",
                    "Новость дня:", "Суть:")


def build_final_post(article: Article, body_text: str, topic: str,
                     skip_clean: bool = False) -> str:
    text = body_text.strip() if skip_clean else strip_service_lines(body_text)

    if not text.endswith(('.', '!', '?')):
        text += '.'

    hashtags = Topic.hashtags(topic)
    source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
    return f"{text}\n\n{hashtags}{source_link}{DISCLAIMER}".strip()


def body_of(post_text: str) -> str:
    return post_text.split('\n\n🔗 <a href="', 1)[0].strip()


def _build_prompt(article: Article, is_block_topic: bool) -> str:
    current_year = datetime.now(timezone.utc).year
    date_instruction = (
        f"ВАЖНО: Если в новости явно не указан год, НЕ пиши никакой год вообще. "
        f"Если год упоминается в новости, используй ТОЛЬКО его. Ни в коем случае не используй "
        f"2024 или другие годы, если они не присутствуют в тексте новости. "
        f"Если нужно указать год, используй только тот, что есть в новости, или текущий {current_year} год, "
        f"но только если он логически вытекает из контекста. Не добавляй год искусственно."
    )
    subject_instruction = (
        "В ПЕРВОМ ПРЕДЛОЖЕНИИ обязательно назови КОНКРЕТНОГО СУБЪЕКТА новости: "
        "компанию, страну, университет, ФИО учёных, организацию. "
        "Если в новости субъект не указан явно, напиши «организация не указана». "
        "Не пиши просто «исследователи» или «аналитики» — нужна конкретика."
    )
    no_labels_instruction = (
        "ВАЖНО: пиши сплошным связным текстом БЕЗ заголовков и служебных слов-меток в начале "
        "предложений или абзацев — никаких «Суть:», «Значение:», «Вступление:», «Последствия:», "
        "«Новость:», «Новость дня:», «Заголовок:», «Содержание:». Читатель сам поймёт структуру "
        "по смыслу текста, разделы подписывать не нужно."
    )
    content = article.full_text or article.summary

    if is_block_topic:
        return f"""Ты — редактор Telegram-канала про блокировки и цифровые ограничения в РФ. Напиши краткий, но законченный пост по новости.

НОВОСТЬ:
Заголовок: {article.title}
Содержание: {content[:2000]}
Источник: {article.source}

**О чём должен рассказать пост (связным текстом, БЕЗ подписанных разделов):**
1. Кратко введи в тему одним предложением.
2. Что именно произошло: факты — кто, что, когда, как.
3. Какие последствия для пользователей или индустрии.

**Требования:**
- Нейтральный, информативный тон, без оценок.
- Не упоминай власть, правительство, Путина – только технические детали.
- Не задавай вопросов читателям.
- Длина: строго 600–800 символов.
- Пост должен быть связным и читаться как единое целое.
- {no_labels_instruction}
- {subject_instruction}
- {date_instruction}

ПОСТ:"""

    return f"""Ты — редактор Telegram-канала про AI и технологии. Напиши краткий, но законченный пост по новости. Твой пост должен быть понятен даже тем, кто не читал исходную статью.

НОВОСТЬ:
Заголовок: {article.title}
Содержание: {content[:2000]}
Источник: {article.source}

**О чём должен рассказать пост (связным текстом, БЕЗ подписанных разделов):**
1. Кратко введи в тему одним предложением.
2. Что именно произошло: факты — кто, что, где, когда. Перескажи своими словами, но сохрани все ключевые факты.
3. Почему это важно для индустрии, пользователей или общества (1–2 предложения).

**Требования:**
- Пиши нейтрально, без оценок и лишних эмоций.
- Не задавай вопросов читателям.
- Не используй слова «власть», «правительство», «путин» и т.п.
- Избегай штампов: «стоит отметить», «важно понимать», «давайте разберёмся».
- Длина: строго 700–1000 символов (не меньше 700, не больше 1000).
- Пост должен быть связным, читаться как единое целое и не обрываться на полуслове.
- {no_labels_instruction}
- {subject_instruction}
- {date_instruction}

ПОСТ:"""


def _strip_prefixes(raw: str) -> str:
    text = raw.strip()
    for pref in SUMMARY_PREFIXES:
        if text.upper().startswith(pref.upper()):
            text = text[len(pref):].strip()
    return text


def _validate_generated(text: str, min_len: int) -> Tuple[bool, str]:
    ok, reason = is_valid_post_text(text, min_len)
    if not ok:
        return False, reason
    if not text[0].isupper():
        return False, "TEXT_NOT_CAPITALIZED"
    water_count = sum(1 for phrase in WATER_PHRASES if phrase in text.lower())
    if water_count >= 3:
        return False, f"TEXT_WATER ({water_count})"
    if has_repeated_sentences(text, config.max_repeat_sentences):
        return False, "TEXT_REPEATED_SENTENCES"
    return True, "OK"


async def generate_summary(article: Article, llm: Optional[GroqLLM] = None) -> Optional[str]:
    """Сгенерировать тело поста. None — материал не годится, пробуем следующий."""
    llm = llm or GroqLLM()
    logger.info(f"📝 Генерация: {article.title[:55]}...")

    text_for_topic = article.text
    topic = Topic.detect(text_for_topic)
    is_block_topic = any(kw in text_for_topic.lower() for kw in BLOCK_KEYWORDS)
    prompt = _build_prompt(article, is_block_topic)

    for attempt in range(config.groq_retries_per_model):
        try:
            raw = await llm.complete(prompt, max_tokens=1500, temperature=0.7 + 0.1 * attempt)
        except LLMError as e:
            logger.error(f"  ❌ Генерация не удалась: {e}")
            return None

        cleaned = strip_service_lines(_strip_prefixes(raw))
        logger.info(f"  ℹ️ raw_len={len(raw)} cleaned_len={len(cleaned)}")

        if not is_block_topic and "SKIP" in raw.upper()[:10]:
            logger.info("  ⏭️ SKIP (не подходит)")
            return None

        ok, reason = _validate_generated(cleaned, config.min_post_length)
        if not ok:
            logger.warning(f"  ⚠️ reject before final build: {reason}")
            continue

        final = build_final_post(article, cleaned, topic, skip_clean=True)
        ok_final, reason_final = is_valid_post_text(body_of(final), config.min_post_length)
        if not ok_final:
            logger.warning(f"  ⚠️ reject after final build: {reason_final}")
            continue

        logger.info(f"  ✅ body={len(cleaned)} symb, final={len(final)} symb")
        return final

    return None
