"""Вовлечение (ТЗ §6): серийные рубрики, просьбы о проверке, сбор случаев, опросы.

Принципы, которые здесь зашиты:
- вовлечение встроено в содержание, а не «А что вы думаете?» в конце;
- бот не утверждает, что провёл тест или опрос аудитории, которого не было;
- рубрики не идут подряд и не повторяются чаще кулдауна;
- опрос появляется только тогда, когда он собирает полезные данные по теме материала.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from .config import config
from .models import Article, PostFormat, Topic

logger = logging.getLogger(__name__)

# Оформление серийных рубрик (ТЗ §6.5): узнаваемый заголовок без визуального шума
RUBRIC_EMOJI = {
    "Проверка недели": "🔬",
    "Что делать": "🧰",
    "Что изменилось": "🔄",
    "Разбор блокировки": "🧩",
    "Полезная находка": "🔧",
}

# Рубрики — не формат, а серия: одна и та же не должна идти подряд
RUBRIC_MIN_GAP_POSTS = 2


@dataclass
class RubricPlan:
    """Рубрика, под которой выйдет материал. header — готовая первая строка."""

    name: str
    header: str


def _post_rubric(post: dict) -> str:
    return str(post.get("rubric") or "").strip()


def _post_date(post: dict) -> Optional[datetime]:
    raw = post.get("date") or post.get("posted_date")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def rubric_last_seen_days(name: str, recent_posts: Sequence[dict],
                          now: Optional[datetime] = None) -> Optional[float]:
    """Сколько дней назад рубрика выходила последний раз. None — не выходила."""
    now = now or datetime.now(timezone.utc)
    for post in recent_posts:
        if _post_rubric(post) != name:
            continue
        date = _post_date(post)
        if date is None:
            return None
        return max(0.0, (now - date).total_seconds() / 86400)
    return None


def choose_rubric(fmt: str, recent_posts: Sequence[dict],
                  now: Optional[datetime] = None) -> Optional[RubricPlan]:
    """Рубрика для формата или None, если показывать её рано (ТЗ §6.5)."""
    name = PostFormat.RUBRIC.get(fmt, "")
    if not name:
        return None

    tail = [_post_rubric(p) for p in recent_posts[:RUBRIC_MIN_GAP_POSTS]]
    if name in tail:
        return None

    days = rubric_last_seen_days(name, recent_posts, now)
    if days is not None and days < config.rubric_cooldown_days:
        return None

    emoji = RUBRIC_EMOJI.get(name, "📌")
    return RubricPlan(name=name, header=f"{emoji} {name}")


@dataclass
class EngagementRequest:
    """Просьба к читателю, которую модель встраивает в текст (ТЗ §6.1–6.3)."""

    kind: str        # hypothesis | cases | continue
    instruction: str


HYPOTHESIS_TERMS = (
    "перестал", "не работает", "не подключа", "сбой", "отказ",
    "обновлен", "блокир", "замедл", "ошибк", "обрыв",
)
CASE_TERMS = (
    "массов", "жалоб", "пользовател", "провайдер", "оператор", "регион",
    "у части", "сообщают",
)
CLIENT_TERMS = ("клиент", "приложен", "karing", "happ", "v2rayn", "sing-box", "hiddify")


def _matches(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _match_score(text: str, terms: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def engagement_request(article: Article, post_format: str,
                       popular_topics: Optional[Sequence[str]] = None) -> Optional[EngagementRequest]:
    """Одна встроенная механика вовлечения или ничего.

    Выбирается по материалу, а не случайно: гипотеза — когда есть сбой/обновление,
    сбор случаев — когда источник говорит о массовости, продолжение — когда тема
    уже показала охват выше среднего.
    """
    text = f"{article.title} {article.summary or ''}"
    topic = Topic.detect(article.text)
    popular = set(popular_topics or [])

    if post_format in (PostFormat.HOWTO, PostFormat.ANALYSIS) and _matches(text, HYPOTHESIS_TERMS):
        return EngagementRequest(
            kind="hypothesis",
            instruction=(
                "В конце — одна проверяемая просьба: что именно читателю проверить и какой "
                "результат сообщить (конфигурация, клиент, провайдер, наблюдаемое поведение). "
                "Пиши как гипотезу: «похоже, дело в …», «проверим на нескольких конфигурациях». "
                "Не утверждай, что проверка уже проведена и что редакция её делала."
            ),
        )

    if post_format == PostFormat.CONSEQUENCE and topic in (Topic.BYPASS, Topic.BLOCK) \
            and _matches(text, CASE_TERMS):
        return EngagementRequest(
            kind="cases",
            instruction=(
                "В финале — одна просьба прислать свой случай: клиент, провайдер/регион и что "
                "именно происходит. Нужно понять, проблема массовая или зависит от провайдера. "
                "Без «поделитесь мнением» и без обещаний, которых бот не выполнит."
            ),
        )

    if topic in popular and post_format in (PostFormat.ANALYSIS, PostFormat.EXPERIMENT):
        return EngagementRequest(
            kind="continue",
            instruction=(
                "Свяжи материал с темой, которая уже выходила в канале: одной фразой, без "
                "пересказа прошлого поста. Если новых фактов нет — не упоминай прошлый материал."
            ),
        )

    return None


def apply_engagement(body: str, request: Optional[EngagementRequest]) -> str:
    """Проверить, что механика вовлечения действительно попала в текст.

    Текст не переписывается: вовлечение заложено в промпт, здесь фиксируется,
    есть ли в готовом тексте просьба к читателю (нужно для логов и тестов).
    """
    if request is None:
        return body
    lowered = body.lower()
    markers = {
        "hypothesis": ("провер", "сообщ", "напиш", "уточн"),
        "cases": ("сообщ", "напиш", "пришл", "укаж"),
        "continue": ("возвраща", "продолж", "как писали", "в прошл"),
    }[request.kind]
    if not any(marker in lowered for marker in markers):
        logger.warning(f"  ⚠️ вовлечение ({request.kind}) не попало в текст")
    return body


# ===== опросы (ТЗ §6.4) =====
# Правило: опрос — продолжение материала, а не способ поднять активность.
# Ключ выбирается по теме поста, и один и тот же вопрос не повторяется раньше
# POLL_REUSE_DAYS. Если все подходящие вопросы на кулдауне — опроса нет.

@dataclass
class PollSpec:
    question: str
    options: List[str]
    key: str = ""
    multiple: bool = False
    anonymous: bool = True
    # poll — обычный опрос, quiz — викторина с правильным ответом (Telegram type=quiz)
    kind: str = "poll"
    correct_option_id: Optional[int] = None
    explanation: str = ""


@dataclass(frozen=True)
class QuizTemplate:
    question: str
    options: Tuple[str, ...]
    correct: int
    explanation: str


POLL_TEMPLATES = {
    # обход блокировок и VPN
    "connection": (
        "Соединение сейчас работает?",
        ["Да, всё работает", "Частично", "Нет, не подключается", "Не проверял"],
    ),
    "client_problem": (
        "На каком клиенте возникла проблема?",
        ["Karing", "v2rayN / HAPP", "sing-box", "Другой"],
    ),
    "protocol": (
        "Какой протокол у вас работает в этой сети?",
        ["VLESS/Reality", "XHTTP", "Hysteria2", "Ни один не подключается"],
    ),
    "client_choice": (
        "Каким клиентом пользуетесь чаще?",
        ["Karing", "v2rayN / HAPP", "sing-box", "Hiddify"],
    ),
    # ИИ и техника — основная тематика канала
    "llm_tools": (
        "Каким ИИ-инструментом пользуетесь чаще?",
        ["ChatGPT", "Claude", "Gemini", "DeepSeek", "Другое"],
    ),
    "ai_pay": (
        "Платите за ИИ-подписки?",
        ["Да, одну", "Да, несколько", "Только бесплатные", "Не пользуюсь"],
    ),
    "local_ai": (
        "Запускаете модели локально?",
        ["Да, на GPU", "Да, только на CPU", "Нет, пользуюсь облаком", "Не запускаю"],
    ),
    "ai_work": (
        "Используете ИИ в работе?",
        ["Каждый день", "Несколько раз в неделю", "Редко", "Не использую"],
    ),
}

VPN_TOPICS = (Topic.BYPASS, Topic.BLOCK, Topic.WHITELIST)

QUIZ_BANK = {
    # обход блокировок
    "vpn_dpi": QuizTemplate(
        "Что делает DPI в сети провайдера?",
        ("Анализирует содержимое пакетов и блокирует по признакам",
         "Шифрует трафик абонента",
         "Ускоряет доставку пакетов",
         "Выдаёт абоненту новый IP"),
        0,
        "DPI смотрит не только адреса: SNI, заголовки, шаблоны рукопожатия.",
    ),
    "vpn_reality": QuizTemplate(
        "Чем VLESS/Reality отличается от обычного TLS-туннеля?",
        ("Шифрует трафик слабее",
         "Маскируется под чужой настоящий сайт",
         "Работает только по UDP",
         "Не требует клиента"),
        1,
        "Reality подставляет TLS-параметры чужого популярного сайта, и фильтру нечего сверять со списком.",
    ),
    "vpn_mtu": QuizTemplate(
        "Соединение поднимается, но страницы не грузятся. Что проверить первым?",
        ("Пароль от Wi-Fi",
         "Часовой пояс",
         "MTU и MSS",
         "Версию браузера"),
        2,
        "При слишком большом MTU рукопожатие проходит, а крупные пакеты режутся.",
    ),
    # ИИ
    "ai_context": QuizTemplate(
        "Что такое контекстное окно модели?",
        ("Сколько текста модель видит за один запрос",
         "Сколько она весит на диске",
         "Сколько запросов в минуту держит",
         "Размер её базы знаний"),
        0,
        "Контекстное окно — объём текста, который модель обрабатывает за один раз, вместе с ответом.",
    ),
    "ai_rag": QuizTemplate(
        "Зачем к модели подключают RAG?",
        ("Чтобы ускорить обучение",
         "Чтобы уменьшить модель",
         "Чтобы отключить фильтры",
         "Чтобы отвечать по внешним документам"),
        3,
        "RAG подмешивает в запрос найденные фрагменты документов, и ответ опирается на них, а не на память модели.",
    ),
    "ai_quant": QuizTemplate(
        "Что даёт квантизация модели?",
        ("Больше параметров",
         "Меньше размер и требования к памяти",
         "Полную точность вычислений",
         "Доступ к интернету"),
        1,
        "Квантизация переводит веса в более короткие числа: модель меньше и быстрее, точность немного падает.",
    ),
}

PROBLEM_TERMS = ("перестал", "не работает", "не подключа", "сбой", "обрыв", "замедл", "отвалил")
PROTOCOL_TERMS = ("vless", "xhttp", "reality", "hysteria", "протокол")
VPN_TERMS = ("vless", "xhttp", "reality", "hysteria", "wireguard", "v2ray", "обход", "dpi")
CLIENT_NAMES = ("karing", "happ", "v2rayn", "sing-box", "hiddify")
PAY_TERMS = ("подписк", "тариф", "цен", "платн", "$")
LLM_TERMS = ("gpt", "chatgpt", "claude", "gemini", "deepseek", "llm", "модел", "нейросет")
LOCAL_TERMS = ("локальн", "gpu", "видеокарт", "nvidia", "оперативн")

# К какому материалу какой вопрос викторины ближе (для порядка выбора)
QUIZ_TRIGGERS = {
    "vpn_dpi": ("dpi", "блокиров", "фильтр", "провайдер"),
    "vpn_reality": ("vless", "reality", "xhttp", "tls", "маскир"),
    "vpn_mtu": ("mtu", "mss", "не груз", "зависа", "перестал", "не работает"),
    "ai_context": ("контекст", "окно", "токен", "модел"),
    "ai_rag": ("rag", "поиск", "документ", "база знаний", "выдач"),
    "ai_quant": ("квант", "локальн", "gpu", "памят", "vram", "gguf"),
}


def _is_vpn_material(text: str, topic: str) -> bool:
    return (topic in VPN_TOPICS or _matches(text, VPN_TERMS) or _matches(text, CLIENT_NAMES))


def _poll_keys_for(text: str, topic: str) -> List[str]:
    """Ключи опросов, уместных для материала, в порядке приоритета."""
    if _is_vpn_material(text, topic):
        keys = []
        if _matches(text, PROBLEM_TERMS):
            keys.append("connection")
            if _matches(text, CLIENT_NAMES):
                keys.append("client_problem")
        if _matches(text, PROTOCOL_TERMS) or topic in (Topic.BYPASS, Topic.BLOCK):
            keys.append("protocol")
        # любой VPN-материал: спросить, чем пользуется читатель, уместно всегда
        keys.append("client_choice")
        return keys

    keys = []
    if _matches(text, PAY_TERMS):
        keys.append("ai_pay")
    if _matches(text, LLM_TERMS):
        keys.append("llm_tools")
    if _matches(text, LOCAL_TERMS):
        keys.append("local_ai")
    keys.append("ai_work")
    return keys


def _quiz_keys_for(text: str, topic: str) -> List[str]:
    """Вопросы викторины для материала: сначала по совпадению темы, потом остальные."""
    prefix = "vpn_" if _is_vpn_material(text, topic) else "ai_"
    keys = [key for key in QUIZ_BANK if key.startswith(prefix)]
    matched = [key for key in keys if _matches(text, QUIZ_TRIGGERS.get(key, ()))]
    # чем больше совпадений с материалом, тем раньше вопрос; при равенстве — порядок банка
    matched.sort(key=lambda key: _match_score(text, QUIZ_TRIGGERS.get(key, ())), reverse=True)
    return matched + [key for key in keys if key not in matched]


def _key_on_cooldown(key: str, recent: dict, days: int,
                     now: Optional[datetime] = None) -> bool:
    """Был ли этот вопрос задан раньше, чем `days` дней назад."""
    raw = (recent or {}).get(key)
    if not raw:
        return False
    try:
        when = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - when).total_seconds() < days * 86400


def _days_since(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        when = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def build_poll(article: Article, post_format: str, state: Optional[dict] = None) -> Optional[PollSpec]:
    """Опрос или викторина по теме материала, или None.

    state: {"polls_today": int, "posts_since_last_poll": int,
            "recent_keys": {key: ts}, "quiz_last_at": ts}
    — предохранители, чтобы вовлечение не превратилось в способ поднять активность
    и не повторяло один и тот же вопрос.
    """
    if not config.polls_enabled:
        return None
    state = state or {}
    if int(state.get("polls_today", 0)) >= config.poll_daily_limit:
        return None
    if int(state.get("posts_since_last_poll", 10 ** 6)) < config.poll_min_posts_between:
        return None

    text = article.text.lower()
    topic = Topic.detect(article.text)
    recent = state.get("recent_keys") or {}

    # викторина идёт реже опроса: не чаще раза в QUIZ_EVERY_DAYS, каждый вопрос — раз в QUIZ_REUSE_DAYS
    if config.quizzes_enabled:
        since_quiz = _days_since(state.get("quiz_last_at"))
        if since_quiz is None or since_quiz >= config.quiz_every_days:
            for key in _quiz_keys_for(text, topic):
                if _key_on_cooldown(key, recent, config.quiz_reuse_days):
                    continue
                template = QUIZ_BANK[key]
                return PollSpec(
                    key=key,
                    question=template.question,
                    options=list(template.options),
                    kind="quiz",
                    correct_option_id=template.correct,
                    explanation=template.explanation,
                )

    for key in _poll_keys_for(text, topic):
        if _key_on_cooldown(key, recent, config.poll_reuse_days):
            continue
        question, options = POLL_TEMPLATES[key]
        return PollSpec(key=key, question=question, options=list(options))

    logger.info("📊 Опрос не отправлен: подходящие вопросы на кулдауне")
    return None


__all__ = [
    "RubricPlan", "choose_rubric", "rubric_last_seen_days",
    "EngagementRequest", "engagement_request", "apply_engagement",
    "PollSpec", "build_poll", "POLL_TEMPLATES",
]
