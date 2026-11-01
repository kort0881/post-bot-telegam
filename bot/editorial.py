"""Редакционная логика: пять форматов, подача §5.1, промпты §7, приёмка текста.

Ключевые принципы (ТЗ §5, §5.1, §7, §8):
- каждый формат имеет свою структуру, а не общий пересказ;
- текст строится как ТРИГГЕР → ИНТРИГА → РАСКРЫТИЕ → ПОЛЬЗА → РЕАКЦИЯ;
- факты отделяются от предположений, выдуманные цифры и «провели тест» запрещены;
- универсальный дисклеймер убран, хештегов 1–3, ссылка на первоисточник остаётся.
"""
import logging
import re
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

STYLE_RULES = """# РОЛЬ

Ты автор Telegram-канала. Ты пишешь для читателя, который листает ленту с телефона и дочитает только то, что сразу зацепило. Тон: живой, информированный, без пафоса, как человек, который разобрался в теме и рассказывает коллеге. Пиши от первого лица («мы», «я») только там, где это уместно по смыслу; новость не нужно превращать в личный дневник.

На входе ты получаешь исходный материал (новость, статью, заметку, тезисы). Пост строишь только на нём.

# СТРУКТУРА ПОСТА

1. Первая фраза: что произошло и почему это важно читателю. Без подводок, без контекста «издалека».
2. Вторая и третья фразы: самая жёсткая конкретика (цифра, имя, срок, масштаб), из которой сам возникает вопрос «и что дальше?».
3. Остальные абзацы: каждый приносит новый факт, последствие или деталь. Если абзац пересказывает предыдущий другими словами, удали его.
4. Финал: см. раздел «Окончание».

Это связный рассказ, а не анкета. Запрещены рубрики и подзаголовки внутри текста: «Что изменилось:», «Кого касается:», «Что делать:», «Главное:», «Итог:» и любые их аналоги. Исключение: если в исходнике есть пошаговая инструкция, её можно оформить коротким списком.

# ВЁРСТКА (ЭКРАН ТЕЛЕФОНА)

- Абзацы по 1-4 предложения, между абзацами пустая строка.
- Длина: 600-900 знаков для новости, до 1500 для разбора. Если исходник бедный, пиши короче, а не раздувай текст.
- Предложения короткие и средние. Одно предложение несёт одну мысль.
- Жирным можно выделить максимум 1-2 ключевых факта на весь пост. Курсив и подчёркивание не использовать.
- Эмодзи: не больше двух на пост и только как маркеры списка или один акцент в начале. Не ставить эмодзи в конце предложений.
- Хештеги не добавлять, пока их явно не попросили. Ссылки, которых нет в исходнике, не придумывать.
- Длинное тире («—»): не больше двух на весь пост. Там, где можно, заменяй точкой или запятой.

# ФАКТЫ

- Используй только то, что есть в исходнике. Не выдумывай цифры, имена, даты, цитаты, результаты тестов, сравнения и «эксперты считают».
- Отделяй факт от мнения и слуха: «по данным [источника]», «компания утверждает», «пока не подтверждено». Называй источник по имени, если он есть в материале.
- Не пиши, что редакция, бот или ты «протестировали», «проверили» или «попробовали» что-либо, если в исходнике нет такого факта.
- Если для ясного поста не хватает данных (нет даты, суммы, источника), не заполняй пробел догадкой. Либо прямо напиши, что деталей пока нет, либо обойди этот пункт.
- Не добавляй оценок («прорыв», «провал», «скандал»), которых нет в материале. Мнение автора допустимо только если оно запрошено, и тогда оно явно помечено как мнение.

# ЧЁРНЫЙ СПИСОК (АНТИ-НЕЙРОСЛОП)

Запрещённые зачины: «В современном мире», «В эпоху цифровизации», «В мире, где…», «Стало известно», «Недавно появилась информация», «Всё чаще», «Сегодня мы поговорим».

Запрещённые слова и обороты: уникальный, инновационный, ключевой, незаменимый, революционный, прорывной, мощный инструмент, погрузимся, раскрыть потенциал, симфония возможностей, несомненно, безусловно, важно отметить, стоит подчеркнуть, стоит отметить, играет важную роль, целый ряд, своего рода, настоящий game changer, новая эра, меняет правила игры, в то время как.

Запрещённые приёмы:
- Антитеза «это не просто X, это Y» и её варианты («не X, а Y» как риторический ход).
- Риторические вопросы к читателю («Задумывались ли вы…», «А вы знали, что…?»).
- Тройные перечисления ради ритма («быстро, удобно, надёжно»), если в исходнике нет трёх реальных пунктов.
- Канцелярит и цепочки родительных падежей («осуществление внедрения функционала оптимизации»). Пиши глаголами: не «произошло повышение цен», а «цены выросли».
- Пустые усилители: «очень», «крайне», «действительно», «буквально», «по-настоящему».
- Обращения вида «друзья», «дорогие читатели», «коллеги».

Пример:
Плохо: «В современном мире нейросети становятся всё более важными. Недавно стало известно, что компания представила инновационный инструмент, который раскроет потенциал бизнеса».
Хорошо: «Компания открыла доступ к генератору отчётов. Он собирает данные из CRM за 10 минут вместо двух часов, по заявлению разработчиков. Пока работает только на тарифе Pro».

# ОКОНЧАНИЕ

Пост заканчивается одним из трёх вариантов:
- последним фактом или следствием, поставленным точкой;
- конкретным действием, которое можно сделать сегодня (с точными условиями из исходника);
- одним точным вопросом по существу материала, на который у читателя есть что ответить.

Запрещены: «Таким образом», «Подводя итог», «В заключение», «Вывод такой», мораль, наставления, «берегите себя», «А что думаете вы? Пишите в комментариях», «Ставьте лайк», «Подписывайтесь». Не заканчивай пост шаблонным вопросом.

# САМОПРОВЕРКА ПЕРЕД ВЫДАЧЕЙ

Мысленно пройди по списку и исправь пост, если хоть один пункт не выполнен:
1. Первая фраза называет событие и причину, по которой оно важно?
2. В тексте нет рубрик-анкет?
3. Каждый факт, цифра и имя есть в исходнике?
4. Нет слов и приёмов из чёрного списка?
5. Не больше двух длинных тире и двух эмодзи?
6. Каждый абзац новый по смыслу и отделён пустой строкой?
7. Финал не содержит морали, призыва или подведения итогов?

# ВЫВОД

Выдавай только готовый текст поста. Без пояснений, приветствий, заголовков вроде «Пост:» и комментариев."""

FORMAT_STRUCTURE = {
    PostFormat.HOWTO: """Как писать (практическое решение):
Связный текст, а не инструкция со списком заголовков. Порядок мысли: что именно сломалось и
у кого, почему так вышло (насколько это подтверждено), затем рабочий ход по шагам обычной
речью, и в конце — где этот ход не поможет. Шаги можно нумеровать только внутри одного
абзаца, если без последовательности теряется смысл.""",
    PostFormat.EXPERIMENT: """Как писать (эксперимент и проверка):
Рассказ об отчёте: что и зачем проверяли, кто и в каких условиях — строго по источнику,
что вышло (цифры только из источника), что это значит для читателя и где границы
применимости. Не утверждать, что проверяла редакция канала.""",
    PostFormat.CONSEQUENCE: """Как писать (последствия новости):
Один случай как отправная точка: что произошло, как это технически выглядело, что из этого
следует для обычного человека и для тех, кто отвечает за инфраструктуру. Показать риск
через сцену или деталь из материала, а не списком «кого это касается». В конце — честный
вывод: что реально стоит проверить у себя, без наставлений.""",
    PostFormat.ANALYSIS: """Как писать (разбор):
Что случилось в двух предложениях, дальше механизм: как это устроено и почему сработало
именно так. Затем — на что влияет и что остаётся неизвестным (гипотезы помечать как
гипотезы). Без лозунгов и без пересказа пресс-релиза.""",
    PostFormat.TOOL: """Как писать (инструмент/находка):
Что это и зачем, кому пригодится в реальной работе, чем отличается от аналогов — только по
фактам источника, и где ограничения. Читатель должен понять, надо ему это или нет.""",
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

# Нельзя утверждать, что проверку проводила редакция (ТЗ §5, формат 2)
BANNED_TEST_CLAIMS = [
    "мы проверили", "мы протестировали", "мы протестировали", "мы измерили",
    "в ходе нашего теста", "наши тесты", "редакция проверила", "редакция протестировала",
    "мы сравнили", "по нашим наблюдениям",
]

# Рубрики-анкеты внутри поста: превращают текст в чек-лист (01-11-26)
BANNED_HEADINGS = [
    "что изменилось", "кого это касается", "какие функции", "нужно ли менять",
    "что может последовать", "что делать", "кому это нужно", "чем отличается",
    "где ограничения", "плюсы и минусы", "итог", "вывод",
]

BANNED_ENDINGS = [
    "а что вы думаете", "как думаете", "пишите в комментариях", "делитесь мнением",
    "ставьте лайк", "подписывайтесь",
]

QUALITY_PROMPT = """Оцени готовую публикацию для Telegram-канала про блокировки, VPN, сети и ИИ.
Ничего не переписывай. Верни строго JSON:

{{"first_line": 0-10, "intrigue": 0-10, "read_to_end": 0-10, "value": 0-10, "share_potential": 0-10, "problems": ["..."], "clicbait": true|false}}

Оценивай честно: если материал слабый — низкие баллы, а не вежливые восьмёрки.
Проверь грамотность: орфография, согласование, падежи, транслит вроде «tunels» вместо
«туннели». Каждую такую ошибку впиши в problems — текст с ними не публикуется.
Проверь, что все утверждения есть в исходнике: додуманные детали (цифры, имена,
«по данным разведки») — тоже в problems.

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
        # одной цепкой первой строки мало: пустой по сути текст не публикуем
        return (not self.clicbait
                and self.first_line >= config.quality_hook_min
                and self.intrigue >= config.quality_hook_min
                and self.value >= 6.0
                and self.read_to_end >= 6.0
                # баллы выше порога не спасают текст, у которого модель нашла
                # несколько содержательных замечаний (мало конкретики, нет фактов)
                and len(self.problems) <= config.quality_max_problems)

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


def build_prompt(article: Article, assessment: Assessment, fmt: str,
                 extra_guidance: Optional[str] = None) -> str:
    low, high = POST_LENGTH_LIMITS.get(fmt, POST_LENGTH_LIMITS["consequence"])
    content = article.full_text or article.summary or ""
    facts = "\n".join(f"- {f}" for f in assessment.facts) or "- (модель не выделила отдельные факты)"
    main_point = assessment.main_point or article.title

    partial_note = (
        "ВНИМАНИЕ: полный текст статьи недоступен, есть только заголовок и краткое описание. "
        "Не додумывай деталей, которых нет в этих данных."
        if not article.full_text else ""
    )

    engagement_block = (
        "\nВСТРОЕННОЕ ВОВЛЕЧЕНИЕ (одна механика, не шаблонный вопрос в конце):\n"
        f"{extra_guidance}\n"
        if extra_guidance else ""
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
{engagement_block}

{STYLE_RULES}

Длина: {low}–{high} символов. Не добивай объём ради объёма.
Пиши сплошным текстом без служебных меток («Суть:», «НОВОСТЬ:», «Формат:»).
Ссылку на источник и хештеги добавит система — их не пиши.

ПУБЛИКАЦИЯ:"""


META_LEAKS = (
    "ссылка на источник будет добавлена системой",
    "ссылка будет добавлена",
    "ссылки будут добавлены системой",
    "хештеги добавит система",
    "хештеги добавит система",
    "ссылка на источник добавлена системой",
    "здесь могла быть ваша ссылка",
)

BOLD_HEADING = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def normalize_formatting(text: str) -> str:
    """Привести разметку модели к тому, что Telegram показывает как разметку.

    Модель любит markdown (**жирный**), а канал отправляется с parse_mode=HTML:
    звёздочки иначе видны в тексте.
    """
    cleaned = BOLD_HEADING.sub(lambda m: f"<b>{m.group(1).strip()}</b>", text)
    cleaned = re.sub(r"__(.+?)__", lambda m: f"<b>{m.group(1).strip()}</b>", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?m)^#{1,6}\s*", "", cleaned)          # markdown-заголовки
    cleaned = re.sub(r"(?m)^\s*[-*]\s+", "• ", cleaned)       # маркеры списков
    cleaned = re.sub(r"<b>\s*</b>", "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def strip_meta_leaks(text: str) -> str:
    """Убрать служебные фразы, которые модель пишет для редактора, а не для читателя."""
    result = text
    for phrase in META_LEAKS:
        result = re.sub(rf"(?i)[^\n.!?]*{re.escape(phrase)}[^\n.!?]*[.!?]?", " ", result)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", result)).strip()


def check_style(text: str) -> Tuple[bool, str]:
    lowered = text.lower().strip()
    for phrase in BANNED_OPENINGS:
        if lowered.startswith(phrase):
            return False, f"BANNED_OPENING ({phrase})"
    for phrase in BANNED_HEADINGS:
        if re.search(rf"(?m)^\s*(?:<b>)?\s*{re.escape(phrase)}", lowered):
            return False, f"CHECKLIST_HEADING ({phrase})"
    for phrase in BANNED_CLICKBAIT:
        if phrase in lowered:
            return False, f"CLICKBAIT ({phrase})"
    for phrase in BANNED_TEST_CLAIMS:
        if phrase in lowered:
            return False, f"FAKE_TEST_CLAIM ({phrase})"
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
    extra_guidance: Optional[str] = None,
) -> Optional[GeneratedPost]:
    """Сгенерировать текст формата с приёмкой и самопроверкой (§5.1 п.6)."""
    llm = llm or GroqLLM()
    prompt = build_prompt(article, assessment, fmt, extra_guidance)
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

        body = normalize_formatting(strip_meta_leaks(strip_service_lines(raw)))
        ok, reason = validate_post(body, fmt)
        if not ok:
            logger.warning(f"  ⚠️ [{fmt}] отклонено по форме: {reason}")
            if reason.startswith("TEXT_TOO_LONG"):
                low, high = POST_LENGTH_LIMITS.get(fmt, POST_LENGTH_LIMITS["consequence"])
                reasons.append(f"текст {len(body)} символов, лимит {high} — сократи до {low}–{high}")
            elif reason.startswith("TEXT_TOO_SHORT"):
                low, high = POST_LENGTH_LIMITS.get(fmt, POST_LENGTH_LIMITS["consequence"])
                reasons.append(f"текст {len(body)} символов, нужно {low}–{high} — добавь фактов из источника")
            else:
                reasons.append(reason)
            continue

        quality = await rate_post(body, llm)
        logger.info(f"  📊 самопроверка [{fmt}]: {quality.as_dict()}")
        if not quality.passed:
            if quality.problems:
                reasons.extend(quality.problems)
            else:
                reasons.append(
                    f"слабый вход: первая строка {quality.first_line}/10, "
                    f"интрига {quality.intrigue}/10 — перепиши подачу"
                )
            logger.warning(f"  ⚠️ [{fmt}] не прошло самопроверку: {quality.problems}")
            continue

        return GeneratedPost(body=body, post_format=fmt, quality=quality, attempts=attempt)

    logger.warning(f"  ⛔ [{fmt}] текст не принят после {config.quality_attempts} попыток")
    return None


def build_post_text(article: Article, body: str, fmt: str, topic: str,
                    include_rubric: bool = False, rubric_header: str = "") -> str:
    """Собрать публикацию: рубрика + текст + 1–3 хештега + ссылка. Без дисклеймера (§8)."""
    text = body.strip()
    if rubric_header:
        text = f"{rubric_header}\n\n{text}"
    if not text.endswith(('.', '!', '?')):
        text += '.'

    hashtags = Topic.hashtags(topic)
    if include_rubric or rubric_header:
        rubric = f"#{PostFormat.RUBRIC.get(fmt, '').replace(' ', '').lower()}"
        if rubric != "#":
            hashtags = f"{hashtags} {rubric}"

    source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
    return f"{text}\n\n{hashtags}{source_link}".strip()


def short_rubric(fmt: str) -> str:
    return PostFormat.RUBRIC.get(fmt, "")
