"""Команды администратора (ТЗ §11): очередь, топ, статистика, отчёт, режим.

Бот публикации работает по расписанию одним прогоном, поэтому управление
вынесено в отдельный процесс (`python adminbot.py`): он слушает команды только
от ID из ADMIN_IDS и работает с той же базой SQLite.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from aiogram import Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .analytics import effective_views, render_report, weekly_report
from .assessment import assess_article
from .config import config
from .db import POSTING_MODES, PostedManager
from .editorial import build_post_text, generate_post, validate_post
from .engagement import RUBRIC_EMOJI
from .llm import DailyQuotaExhausted, LLMError
from .models import Article, Topic
from .textutils import parse_db_datetime

logger = logging.getLogger(__name__)

HELP_TEXT = """Команды канала «Доступ к интернету»

/queue [n] — очередь материалов
/top [n] — лучшие найденные темы
/stats — сводка публикаций
/report — отчёт за неделю
/publish [id] — опубликовать материал (без id — лучший в очереди)
/skip [id] — убрать материал из очереди
/why <url> — почему материал был отклонён
/settings — текущие настройки
/mode auto|approval|collect — режим публикации

Режимы: auto — публикует сам; approval — складывает в очередь и ждёт
подтверждения; collect — только собирает материалы, ничего не пишет в канал."""


def is_admin(user_id: Optional[int]) -> bool:
    return bool(user_id) and int(user_id) in config.admin_ids


# ===== представление данных =====

def _short(text: str, limit: int = 70) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_queue(items: List[dict]) -> str:
    if not items:
        return "🗂️ Очередь пуста."
    lines = [f"🗂️ Очередь: {len(items)} материал(ов)"]
    for item in items:
        score = item.get("score")
        score_text = f"{score}/100" if score is not None else "без оценки"
        fmt = item.get("format") or "формат не выбран"
        lines.append(f"#{item['id']} · {score_text} · {fmt}\n   {_short(item['title'])}")
    lines.append("\n/publish <id> — опубликовать, /skip <id> — отклонить")
    return "\n".join(lines)


def format_top(items: List[dict]) -> str:
    if not items:
        return "🎯 Кандидатов нет: очередь пуста."
    lines = ["🎯 Лучшие темы по оценке:"]
    for i, item in enumerate(items, start=1):
        score = item.get("score")
        score_text = f"{score}/100" if score is not None else "—"
        lines.append(f"{i}. [{score_text}] {_short(item['title'])} ({item.get('source') or '—'})")
    return "\n".join(lines)


def format_stats(posted: PostedManager) -> str:
    stats = posted.get_stats()
    rows = posted.stats_rows(config.report_days)
    measured = [r for r in rows if effective_views(r) is not None]
    queue_pending = posted.queue_count("pending")
    queue_all = posted.queue_count("published") + posted.queue_count("skipped")
    formats: dict = {}
    for row in rows:
        key = row.get("post_format") or "—"
        formats[key] = formats.get(key, 0) + 1
    fmt_text = ", ".join(f"{k}: {v}" for k, v in sorted(formats.items(), key=lambda x: -x[1]))
    return (
        f"📊 Статистика\n"
        f"Всего опубликовано: {stats['total_posted']}\n"
        f"Отклонено (с причинами): {stats['total_rejected']}\n"
        f"Очередь: {queue_pending} ждут, {queue_all} решено\n"
        f"За {config.report_days} дн.: {len(rows)} постов, метрики есть у {len(measured)}\n"
        f"Форматы: {fmt_text or '—'}\n"
        f"Режим: {posted.get_posting_mode()}"
    )


def format_settings(posted: PostedManager) -> str:
    return (
        f"⚙️ Настройки\n"
        f"Режим публикации: {posted.get_posting_mode()} (переменная: {config.posting_mode})\n"
        f"Канал: {config.channel_id or '—'}\n"
        f"Порог публикации: {config.publish_min_score}, порог очереди: {config.queue_min_score}\n"
        f"Не больше подряд: формат {config.max_same_format_in_row}, тема {config.max_same_topic_in_row}\n"
        f"Кулдаун рубрики: {config.rubric_cooldown_days} дн.\n"
        f"Опросы: {'вкл' if config.polls_enabled else 'выкл'}, "
        f"не чаще 1 на {config.poll_min_posts_between} постов и {config.poll_daily_limit} в сутки\n"
        f"Очередь: до {config.queue_max_items} материалов, хранение {config.queue_ttl_days} дн.\n"
        f"Админы: {len(config.admin_ids)} id"
    )


def article_from_queue_item(item: dict) -> Article:
    """Собрать статью обратно из payload очереди."""
    payload = item.get("payload") or {}
    published = datetime.now(timezone.utc)
    raw = payload.get("published")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            published = parse_db_datetime(item.get("created_at")) or published
    return Article(
        title=payload.get("title") or item.get("title") or "",
        summary=payload.get("summary") or "",
        link=payload.get("link") or item.get("url") or "",
        source=payload.get("source") or item.get("source") or "",
        published=published,
    )


def rubric_header(name: str) -> str:
    if not name:
        return ""
    return f"{RUBRIC_EMOJI.get(name, '📌')} {name}"


# ===== действия =====

async def publish_item(item_id: Optional[int], posted: PostedManager, runtime, bot) -> Tuple[bool, str]:
    """Сгенерировать и отправить материал из очереди. Без отправки — dry_run."""
    item = posted.queue_item(item_id) if item_id is not None else posted.queue_best()
    if item is None:
        return False, "Материала с таким id нет."
    if item.get("status") != "pending":
        return False, f"Материал #{item['id']} уже в статусе «{item['status']}»."

    article = article_from_queue_item(item)
    if not article.link:
        return False, "В материале нет ссылки на источник."

    try:
        assessment = await assess_article(article, runtime.llm)
    except (LLMError, DailyQuotaExhausted) as e:
        return False, f"Оценка недоступна: {e}"

    fmt = item.get("format") or assessment.post_format
    generated = await generate_post(article, assessment, fmt, runtime.llm)
    if generated is None:
        posted.decide_queue_item(item["id"], "skipped", "генерация не удалась")
        return False, "Текст не прошёл генерацию/приёмку, материал помечен skipped."

    ok, reason = validate_post(generated.body, fmt)
    if not ok:
        posted.decide_queue_item(item["id"], "skipped", f"приёмка: {reason}")
        return False, f"Текст не прошёл приёмку: {reason}"

    topic = item.get("topic") or Topic.detect(article.text)
    text = build_post_text(article, generated.body, fmt, topic,
                           rubric_header=rubric_header(item.get("rubric") or ""))

    try:
        message = await bot.send_message(config.channel_id, text)
    except TelegramAPIError as e:
        return False, f"Telegram не принял пост: {e}"

    message_id = getattr(message, "message_id", None)
    posted.add(article, topic, topic, post_format=fmt, score=item.get("score"),
               message_id=message_id, rubric=item.get("rubric") or None)
    posted.start_post_stats(article, topic, fmt, item.get("score"),
                            item.get("rubric") or "", message_id)
    posted.decide_queue_item(item["id"], "published", "опубликовано вручную",
                             message_id=message_id)
    logger.info(f"✅ Вручную опубликовано #{item['id']}: {article.title[:50]}")
    return True, f"Опубликовано #{item['id']}: {_short(article.title, 60)}"


def skip_item(item_id: Optional[int], posted: PostedManager, reason: str = "скип админом") -> str:
    item = posted.queue_item(item_id) if item_id is not None else posted.queue_best()
    if item is None:
        return "Материала с таким id нет."
    if item.get("status") != "pending":
        return f"Материал #{item['id']} уже в статусе «{item['status']}»."
    posted.decide_queue_item(item["id"], "skipped", reason)
    return f"Материал #{item['id']} убран из очереди."


# ===== роутер =====

def build_router(posted_provider, runtime_provider) -> Router:
    """Роутер команд. posted_provider/runtime_provider — функции, чтобы не держать
    соединение с БД до первой команды (важно для тестов)."""
    router = Router(name="admin")

    async def guard(message: Message) -> bool:
        if is_admin(message.from_user.id if message.from_user else None):
            return True
        logger.warning(f"⛔ Команда от не-админа: {message.from_user.id if message.from_user else '?'}")
        await message.answer("Команда доступна только администратору канала.")
        return False

    def arg_int(raw: Optional[str]) -> Optional[int]:
        if not raw:
            return None
        try:
            return int(raw.strip())
        except ValueError:
            return None

    @router.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        if not await guard(message):
            return
        await message.answer(HELP_TEXT)

    @router.message(Command("queue"))
    async def cmd_queue(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        limit = arg_int(command.args) or 10
        posted = posted_provider()
        await message.answer(format_queue(posted.queue_items(limit=limit)))

    @router.message(Command("top"))
    async def cmd_top(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        limit = arg_int(command.args) or 7
        posted = posted_provider()
        await message.answer(format_top(posted.queue_items(limit=limit)))

    @router.message(Command("stats"))
    async def cmd_stats(message: Message) -> None:
        if not await guard(message):
            return
        await message.answer(format_stats(posted_provider()))

    @router.message(Command("report"))
    async def cmd_report(message: Message) -> None:
        if not await guard(message):
            return
        posted = posted_provider()
        report = weekly_report(posted.stats_rows(config.report_days + 1),
                               days=config.report_days)
        await message.answer(render_report(report))

    @router.message(Command("publish"))
    async def cmd_publish(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        runtime = runtime_provider()
        runtime.init()
        ok, text = await publish_item(arg_int(command.args), posted_provider(), runtime,
                                      runtime.bot)
        logger.info(f"publish: {ok} — {text}")
        await message.answer(("✅ " if ok else "⚠️ ") + text)

    @router.message(Command("skip"))
    async def cmd_skip(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        await message.answer(skip_item(arg_int(command.args), posted_provider()))

    @router.message(Command("why"))
    async def cmd_why(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        raw = (command.args or "").strip()
        if not raw:
            await message.answer("Использование: /why <ссылка на материал>")
            return
        found, reason = posted_provider().get_rejection_reason(raw)
        await message.answer(f"🚫 {reason}" if found else "Материал не найден в отклонённых.")

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        if not await guard(message):
            return
        await message.answer(format_settings(posted_provider()))

    @router.message(Command("mode"))
    async def cmd_mode(message: Message, command: CommandObject) -> None:
        if not await guard(message):
            return
        posted = posted_provider()
        raw = (command.args or "").strip().lower()
        if not raw:
            await message.answer(f"Текущий режим: {posted.get_posting_mode()}.\n"
                                 f"Доступно: {', '.join(POSTING_MODES)}")
            return
        if not posted.set_posting_mode(raw):
            await message.answer(f"Неизвестный режим «{raw}». Доступно: {', '.join(POSTING_MODES)}")
            return
        await message.answer(f"Режим переключён на «{posted.get_posting_mode()}».")

    return router


async def main() -> int:
    """Процесс команд админа (polling). Публикацию по расписанию не трогает."""
    config.validate()
    if not config.admin_ids:
        logger.error("❌ ADMIN_IDS не задан: команды некому разрешить. Останов.")
        return 1

    from .pipeline import Runtime

    runtime = Runtime()
    runtime.init()
    posted = PostedManager(config.db_file)

    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(lambda: posted, lambda: runtime))

    try:
        await runtime.bot.delete_webhook(drop_pending_updates=True)
    except TelegramAPIError as e:
        logger.warning(f"⚠️ delete_webhook: {e}")

    logger.info(f"🤖 Админ-бот запущен, режим публикации: {posted.get_posting_mode()}")
    try:
        await dispatcher.start_polling(runtime.bot)
    finally:
        posted.close()
        await runtime.close()
    return 0


__all__ = [
    "main", "build_router", "is_admin", "format_queue", "format_top", "format_stats",
    "format_settings", "article_from_queue_item", "publish_item", "skip_item", "HELP_TEXT",
]
