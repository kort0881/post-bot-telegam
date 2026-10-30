"""Редакционная логика: пять форматов, подача §5.1, промпты §7, приёмка текста.

Ключевые принципы (ТЗ §5, §5.1, §7, §8):
- каждый формат имеет свою структуру, а не общий пересказ;
- текст строится как ТРИГГЕР → ИНТРИГА → РАСКРЫТИЕ → ПОЛЬЗА → РЕАКЦИЯ;
- факты отделяются от предположений, выдуманные цифры и «провели тест» запрещены;
- универсальный дисклеймер убран, хештегов 1–3, ссылка на первоисточник остаётся.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .assessment import Assessment
from .config import POST_LENGTH_LIMITS, config
from .llm import GroqLLM, LLMError
from .models import Article, PostFormat, Topic
from .textutils import (
    count_sentences,
    has_repeated_sentences,
    is_valid_post_text,
    parse_json_object,
    strip_service_lines,
)

logger = logging.getLogger(__name__)

STYLE_RULES = """Правила текста:
- первая фраза сразу сообщает суть и цепляет: угроза, неожиданность, конфликт, любопытство, выгода, срочность, разоблачение, сравнение или последствия — выбери то, что реально есть в материале;
- после первой строки у читателя должен возникнуть конкретный вопрос, ответ на который даёт текст;
- каждый следующий абзац добавляет новую информацию, а не пересказывает предыдущий;
- отделяй факты от предположений: «по данным источника», «разработчики утверждают» — если это утверждение, а не проверенный факт;
- не добавляй цифры, характеристики, результаты тестов и заявления, которых нет в материале;
- не пиши, что бот или редакция что-то тестировали, если тестирования не было;
- запрещены пустые вступления: «В современном мире», «Стало известно», «Недавно появилась информация», «Всё больше пользователей»;
- запрещены канцелярит, рекламный тон и искусственные сенсации: «Вы не поверите», «Шок», «Срочно! Все должны это знать», «Интернет больше не будет прежним»;
- не заканчивай пост шаблонным вопросом, «А что вы думаете?» и «Пишите в комментариях»;
- финал — конкретное действие, параметр для проверки или следующий вопрос по теме, если он вытекает из материала."""

FORMAT_STRUCTURE = {
    PostFormat.HOWTO: """Структура (практическое решение):
1. Конкретная проблема — что именно перестало работать или ломается.
2. Причина или объяснение — почему так происходит, насколько это подтверждено.
3. Что делать — по шагам, применимо на практике.
4. Какие ограничения есть — где решение не поможет.
5. Ссылка на источник/документацию указывается отдельной строкой (её добавит система).""",
    PostFormat.EXPERIMENT: """Структура (эксперимент и проверка):
1. Что проверяли и зачем.
2. Кто и в каких условиях проводил проверку — строго по источнику.
3. Что получилось: результаты, цифры только из источника.
4. Что это значит для читателя.
5. Где границы применимости результата.
Ни в коем случае не утверждай, что проверку проводила редакция канала.""",
    PostFormat.CONSEQUENCE: """Структура (последствия новости):
1. Что конкретно изменилось.
2. Кого это затрагивает.
3. Что перестанет работать или станет работать иначе.
4. Нужно ли читателю что-то менять.
5. Какие последствия возможны дальше.""",
    PostFormat.ANALYSIS: """Структура (разбор):
1. Что произошло, в одном-двух предложениях.
2. Как это работает технически — механизм, а не лозунги.
3. Почему это важно и на что влияет.
4. Что остаётся неизвестным или спорным.
Гипотезы помечай как гипотезы.""",
    PostFormat.TOOL: """Структура (инструмент/находка):
1. Что это.
2. Для чего нужно.
3. Кому пригодится.
4. Чем отличается от аналогов — только по фактам источника.
5. Ограничения.
6. Ссылка на проект или официальный источник (добавит система).""",
}

FORMAT_STYLE = {
    PostFormat.HOWTO: "Пиши как инженер, который объясняет коллеге: короткие абзацы, конкретные шаги, без воды.",
    PostFormat.EXPERIMENT: "Пиши как автор отчёта о проверке: что измеряли, чем, при каких условиях, что вышло.",
    PostFormat.CONSEQUENCE: "Пиши как аналитик: что изменилось и что из этого следует для обычного пользователя.",
    PostFormat.ANALYSIS: "Пиши как технический разбор: причинно-следственные связи, устройство механизма, без пафоса.",
    PostFormat.TOOL: "Пиши как обзорщик инструментов: назначение, сценарии, ограничения, альтернативы.",
}

BANNED_OPENINGS = [
    "в современном мире", "стало известно", "недавно появилась информация",
    "всё больше пользователей", "все больше пользователей", "не секрет, что",
    "как мы знаем", "в наше время", "на сегодняшний день",
]

BANNED_CLICKBAIT = [
    "вы не поверите", "шок!", "шок:", "срочно! все", "все должны это знать",
    "интернет больше не будет прежним", "никто не ожидал", "это изменит всё навсегда",
    "просто шок",
]

BANNED_ENDINGS = [
    "а что вы думаете", "как думаете", "пишите в комментариях", "делитесь мнением",
    "ставьте лайк", "подписывайтесь",
]

QUALITY_PROMPT = """Оцени готовую публикацию для Telegram-канала про блокировки, VPN, сети и ИИ.
Ничего не переписывай. Верни строго JSON:

{{"first_line": 0-10, "intrigue": 0-10, "read_to_end": 0-10, "value": 0-10, "share_potential": 0-10, "problems": ["..."], "clicbait": true|false}}

Оценивай честно: если материал слабый — низкие баллы, а не вежливые восьмёрки.

ПУБЛИКАЦИЯ:
{text}"""


@dataclass
class QualityReport:
    first_line: float = 0.0
    intrigue: float = 0.0
    read_to_end: float = 0.0
    value: float = 0.0
    share_potential: float = 0.0
    problems: List[str] = field(default_factory=list)
    clicbait: bool = False
    source: str = "heuristic"

    @property
    def passed(self) -> bool:
        return (not self.clicbait
                and self.first_line >= config.quality_hook_min
                and self.intrigue >= config.quality_hook_min)

    def as_dict(self) -> dict:
        return {
            "first_line": self.first_line, "intrigue": self.intrigue,
            "read_to_end": self.read_to_end, "value": self.value,
            "share_potential": self.share_potential, "clicbait": self.clicbait,
            "problems": list(self.problems), "source": self.source,
        }


def choose_format(assessment: Assessment, recent_formats: Optional[List[str]] = None) -> str:
    """Формат материала с учётом баланса §12 и целевого распределения §5."""
    recent_formats = [f for f in (recent_formats or []) if f]
    proposed = assessment.post_format if assessment.post_format in PostFormat.ALL else PostFormat.CONSEQUENCE

    tail = recent_formats[: config.max_same_format_in_row]
    if len(tail) >= config.max_same_format_in_row and all(f == proposed for f in tail):
        # третий подряд — берём формат, который давно не выходил
        counts = {fmt: recent_formats.count(fmt) for fmt in PostFormat.ALL}
        target = {fmt: PostFormat.TARGET_SHARE[fmt] * max(len(recent_formats), 1) for fmt in PostFormat.ALL}
        candidates = [fmt for fmt in PostFormat.ALL if fmt != proposed]
        candidates.sort(key=lambda fmt: (counts[fmt] - target[fmt], PostFormat.TARGET_SHARE[fmt]))
        logger.info(f"  🔁 формат {proposed} повторяется подряд — берём {candidates[0]}")
        return candidates[0]
    return proposed


def build_prompt(article: Article, assessment: Assessment, fmt: str) -> str:
    low, high = POST_LENGTH_LIMITS.get(fmt, POST_LENGTH_LIMITS["consequence"])
    content = article.full_text or article.summary or ""
    facts = "\n".join(f"- {f}" for f in assessment.facts) or "- (модель не выделила отдельные факты)"
    main_point = assessment.main_point or article.title

    partial_note = (
        "ВНИМАНИЕ: полный текст статьи недоступен, есть только заголовок и краткое описание. "
        "Не додумывай детали, которых нет в этих данных."
        if not article.full_text else ""
    )

    return f"""Ты — технический редактор Telegram-канала «Доступ к интернету»: блокировки, обход DPI, VPN, сети, безопасность, ИИ.
Твоя задача — написать публикацию в формате «{PostFormat.TITLES[fmt]}», а не пересказ новости.

{FORMAT_STRUCTURE[fmt]}

{FORMAT_STYLE[fmt]}

Главный подтверждённый повод: {main_point}
Триггер подачи: {assessment.trigger}

Факты из материала (только они достоверны):
{facts}

ИСХОДНЫЙ МАТЕРИАЛ
Заголовок: {article.title}
Источник: {article.source}
Текст: {content[: config.max_fulltext_chars]}
{partial_note}

{STYLE_RULES}

Длина: {low}–{high} символов. Не добивай объём ради объёма.
Пиши сплошным текстом без служебных меток («Суть:», «НОВОСТЬ:», «Формат:»).
Ссылку на источник и хештеги добавит система — их не пиши.

ПУБЛИКАЦИЯ:"""


def check_style(text: str) -> Tuple[bool, str]:
    lowered = text.lower().strip()
    for phrase in BANNED_OPENINGS:
        if lowered.startswith(phrase):
            return False, f"BANNED_OPENING ({phrase})"
    for phrase in BANNED_CLICKBAIT:
        if phrase in lowered:
            return False, f"CLICKBAIT ({phrase})"
    tail = lowered[-160:]
    for phrase in BANNED_ENDINGS:
        if phrase in tail:
            return False, f"BANNED_ENDING ({phrase})"
    if lowered.rstrip().endswith("?"):
        return False, "ENDS_WITH_QUESTION"
    return True, "OK"


def validate_post(text: str, fmt: str) -> Tuple[bool, str]:
    low, high = POST_LENGTH_LIMITS.get(fmt, POST_LENGTH_LIMITS["consequence"])
    ok, reason = is_valid_post_text(text, low, high)
    if not ok:
        return False, reason
    if count_sentences(text) < 3:
        return False, "TEXT_TOO_FEW_SENTENCES"
    if has_repeated_sentences(text, config.max_repeat_sentences):
        return False, "TEXT_REPEATED_SENTENCES"
    return check_style(text)


def parse_quality(raw: str) -> Optional[QualityReport]:
    data = parse_json_object(raw)
    if not data:
        return None

    def num(key):
        try:
            return max(0.0, min(10.0, float(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0.0

    problems = data.get("problems") or []
    if not isinstance(problems, list):
        problems = []
    return QualityReport(
        first_line=num("first_line"),
        intrigue=num("intrigue"),
        read_to_end=num("read_to_end"),
        value=num("value"),
        share_potential=num("share_potential"),
        problems=[str(p)[:200] for p in problems[:5]],
        clicbait=bool(data.get("clicbait", False)),
        source="llm",
    )


async def rate_post(text: str, llm: GroqLLM) -> QualityReport:
    """Самопроверка текста моделью. Без модели — консервативная оценка по форме."""
    try:
        raw = await llm.complete(
            QUALITY_PROMPT.format(text=text[:3500]),
            max_tokens=400, temperature=0.1, json_mode=True,
        )
        report = parse_quality(raw)
        if report:
            # формальные нарушения важнее самооценки модели
            style_ok, style_reason = check_style(text)
            if not style_ok:
                report.clicbait = "CLICKBAIT" in style_reason or report.clicbait
                report.problems.append(style_reason)
            return report
    except LLMError as e:
        logger.warning(f"  ⚠️ Самопроверка недоступна: {e}")

    # Без модели: проверяем форму (длина, стиль, структура) и не мешаем публикации
    ok, reason = validate_post(text, PostFormat.CONSEQUENCE)
    problems = [] if ok else [reason]
    return QualityReport(
        first_line=7.0 if ok else 0.0,
        intrigue=7.0 if ok else 0.0,
        read_to_end=6.0 if ok else 0.0,
        value=6.0 if ok else 0.0,
        share_potential=5.0 if ok else 0.0,
        problems=problems,
        clicbait=not ok,
        source="heuristic",
    )


@dataclass
class GeneratedPost:
    body: str
    post_format: str
    quality: QualityReport
    attempts: int = 1


async def generate_post(
    article: Article,
    assessment: Assessment,
    fmt: str,
    llm: Optional[GroqLLM] = None,
) -> Optional[GeneratedPost]:
    """Сгенерировать текст формата с приёмкой и самопроверкой (§5.1 п.6)."""
    llm = llm or GroqLLM()
    prompt = build_prompt(article, assessment, fmt)
    reasons: List[str] = []

    for attempt in range(1, config.quality_attempts + 1):
        suffix = ""
        if reasons:
            suffix = ("\n\nПрошлый вариант отклонён: " + "; ".join(reasons[:3])
                      + ". Смени подход к первой строке и к интриге, не меняя факты.")
        try:
            raw = await llm.complete(prompt + suffix, max_tokens=1800,
                                     temperature=0.7 + 0.15 * (attempt - 1))
        except LLMError as e:
            logger.error(f"  ❌ Генерация не удалась: {e}")
            return None

        body = strip_service_lines(raw).strip()
        ok, reason = validate_post(body, fmt)
        if not ok:
            logger.warning(f"  ⚠️ [{fmt}] отклонено по форме: {reason}")
            reasons.append(reason)
            continue

        quality = await rate_post(body, llm)
        logger.info(f"  📊 самопроверка [{fmt}]: {quality.as_dict()}")
        if not quality.passed:
            reasons.extend(quality.problems or ["низкая сила первой строки/интриги"])
            logger.warning(f"  ⚠️ [{fmt}] не прошло самопроверку: {quality.problems}")
            continue

        return GeneratedPost(body=body, post_format=fmt, quality=quality, attempts=attempt)

    logger.warning(f"  ⛔ [{fmt}] текст не принят после {config.quality_attempts} попыток")
    return None


def build_post_text(article: Article, body: str, fmt: str, topic: str,
                    include_rubric: bool = False) -> str:
    """Собрать публикацию: текст + 1–3 хештега + ссылка. Без дисклеймера (§8)."""
    text = body.strip()
    if not text.endswith(('.', '!', '?')):
        text += '.'

    hashtags = Topic.hashtags(topic)
    if include_rubric:
        rubric = f"#{PostFormat.RUBRIC.get(fmt, '').replace(' ', '').lower()}"
        if rubric != "#":
            hashtags = f"{hashtags} {rubric}"

    source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
    return f"{text}\n\n{hashtags}{source_link}".strip()


def short_rubric(fmt: str) -> str:
    return PostFormat.RUBRIC.get(fmt, "")
