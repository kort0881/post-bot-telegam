"""Пайплайн прогона: сбор, фильтрация, рейтинг, оценка, генерация, публикация."""
import asyncio
import logging
import os
import signal
from typing import Dict, List, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LinkPreviewOptions

from .assessment import Assessment, assess_article, heuristic_assessment
from .config import RSS_FEEDS, config
from .db import PostedManager
from .editorial import build_post_text, choose_format, generate_post, validate_post
from .extract import enrich_with_full_text
from .feeds import load_all_feeds
from .filters import evaluate_relevance
from .llm import GroqLLM
from .models import Article, PostFormat, Topic
from .rating import (
    RankedCandidate,
    diversify,
    priority_band,
    rank_candidates,
)
from .textutils import (
    get_content_hash,
    get_sorted_word_signature,
    normalize_title,
)

logger = logging.getLogger(__name__)

LOCK_FILE = "bot.lock"


class Runtime:
    """Клиенты Telegram и Groq, созданные лениво (тесты не требуют ключей)."""

    def __init__(self):
        self.bot: Optional[Bot] = None
        self.llm: Optional[GroqLLM] = None

    def init(self) -> None:
        if self.bot is None:
            self.bot = Bot(
                token=config.telegram_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            logger.info("✅ Telegram Bot инициализирован")
        if self.llm is None:
            self.llm = GroqLLM()
            logger.info("✅ Groq client инициализирован")

    async def close(self) -> None:
        if self.bot is not None:
            try:
                await self.bot.session.close()
                logger.info("🔒 Telegram сессия закрыта")
            except Exception as e:
                logger.error(f"❌ Ошибка закрытия Telegram: {e}")


# ====================== ФИЛЬТРАЦИЯ ======================
def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("🔍 Фильтрация...")
    logger.info(f"   Входящих статей: {len(articles)}")

    candidates: List[Article] = []
    seen_normalized_titles = set()
    seen_word_signatures = set()
    seen_content_hashes = set()
    batch_subject_counts: Dict[str, int] = {}

    stats = {
        "not_relevant": 0, "batch_dup": 0, "db_dup": 0, "diversity": 0,
        "subject_limit": 0, "batch_subject": 0, "passed": 0,
    }

    for article in articles:
        ok, reason = evaluate_relevance(article)
        if not ok:
            posted.log_rejected(article, reason)
            stats["not_relevant"] += 1
            continue

        title_normalized = normalize_title(article.title)
        if title_normalized in seen_normalized_titles:
            posted.log_rejected(article, "BATCH_DUP_TITLE")
            stats["batch_dup"] += 1
            continue

        word_sig = get_sorted_word_signature(article.title)
        if word_sig in seen_word_signatures:
            posted.log_rejected(article, "BATCH_DUP_WORDS")
            stats["batch_dup"] += 1
            continue

        content_hash = get_content_hash(f"{article.title} {article.summary}")
        if content_hash and content_hash in seen_content_hashes:
            posted.log_rejected(article, "BATCH_DUP_CONTENT")
            stats["batch_dup"] += 1
            continue

        subject = Topic.detect(article.text)

        if subject != "other" and batch_subject_counts.get(subject, 0) >= config.batch_subject_limit:
            posted.log_rejected(article, f"BATCH_SUBJECT_LIMIT ({subject})")
            stats["batch_subject"] += 1
            continue

        subj_ok, subj_reason = posted.check_subject_limit(subject, article.title)
        if not subj_ok:
            posted.log_rejected(article, subj_reason)
            stats["subject_limit"] += 1
            continue

        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            posted.log_rejected(article, "DB_DUP: " + "; ".join(dup_result.reasons[:3]))
            stats["db_dup"] += 1
            continue

        div_ok, div_reason = posted.check_diversity(subject, article.source)
        if not div_ok:
            posted.log_rejected(article, f"DIVERSITY: {div_reason}")
            stats["diversity"] += 1
            continue

        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)
        batch_subject_counts[subject] = batch_subject_counts.get(subject, 0) + 1

        candidates.append(article)
        stats["passed"] += 1

    logger.info(
        "📊 Фильтр: прошло {passed}, не по теме {not_relevant}, дубли в пачке {batch_dup}, "
        "дубли в БД {db_dup}, лимит темы {subject_limit}/{batch_subject}, разнообразие {diversity}".format(**stats)
    )
    return candidates


# ====================== ВЫБОР ======================
def select_candidates(candidates: List[Article], posted: PostedManager) -> List[RankedCandidate]:
    """Единый рейтинг по всем источникам + мягкие ограничения на разнообразие."""
    if not candidates:
        return []

    recent = posted.get_recent_posts(config.rotation_history_size)
    recent_sources = [p.get('source', '') for p in recent]
    recent_topics = [p.get('topic', '') for p in recent]

    ranked = rank_candidates(candidates, recent_sources=recent_sources, recent_topics=recent_topics)
    ranked = diversify(ranked, max_per_source=2, max_per_topic=2)

    logger.info("🎯 Топ кандидатов по единому рейтингу:")
    for i, item in enumerate(ranked[:10], start=1):
        logger.info(
            f"  {i}. [{item.effective}/{priority_band(item.score.total)}] "
            f"[{item.article.source}] {item.article.title[:60]}"
        )
        logger.info(f"     {item.score.as_dict()}")
    return ranked


# ====================== ПУБЛИКАЦИЯ ======================
async def send_post(article: Article, text: str, posted: PostedManager, bot: Bot,
                    topic: str, post_format: str, score: int) -> bool:
    message_id = None
    for attempt in range(2):
        try:
            message = await bot.send_message(
                config.channel_id,
                text,
                link_preview_options=LinkPreviewOptions(
                    is_disabled=False,
                    url=article.link,
                    prefer_large_media=True,
                    show_above_text=False,
                ),
            )
            message_id = getattr(message, "message_id", None)
            break
        except TelegramAPIError as e:
            logger.error(f"❌ Telegram ошибка отправки (попытка {attempt + 1}): {e}")
            if attempt == 0:
                await asyncio.sleep(3)
            else:
                return False

    logger.info(f"✅ ОПУБЛИКОВАНО [{post_format}/{topic}][{article.source}]: {article.title[:50]}")
    saved = posted.add(article, topic, topic, post_format=post_format,
                       score=score, message_id=message_id)
    if not saved:
        logger.warning(f"⚠️ Пост отправлен, но не сохранён в БД (возможно дубль): {article.title[:50]}")
    return True


# ====================== ПРОГОН ======================
async def check_telegram_connection(bot: Bot) -> bool:
    try:
        logger.info("🔌 Проверка подключения к Telegram...")
        me = await asyncio.wait_for(bot.get_me(), timeout=config.telegram_timeout)
        logger.info(f"✅ Telegram OK: @{me.username}")
        return True
    except asyncio.TimeoutError:
        logger.error("❌ Telegram: Timeout при подключении")
        return False
    except Exception as e:
        logger.error(f"❌ Telegram: {e}")
        return False


async def _evaluate_candidate(article: Article, llm: GroqLLM, extracted: int):
    """Полный текст (пока есть квота на скачивание) + оценка материала."""
    if extracted < config.max_extractions and not article.full_text:
        await enrich_with_full_text(article)
        extracted += 1
    assessment = await assess_article(article, llm)
    return assessment, extracted


def _topic_in_row_blocks(topic: str, recent_topics: List[str]) -> bool:
    tail = recent_topics[: config.max_same_topic_in_row]
    return bool(tail) and len(tail) >= config.max_same_topic_in_row and all(t == topic for t in tail)


async def run_cycle(runtime: Runtime, posted: PostedManager,
                    shutdown_event: Optional[asyncio.Event] = None) -> dict:
    """Один прогон: собрать, отфильтровать, оценить, сгенерировать, опубликовать материал."""
    shutdown_event = shutdown_event or asyncio.Event()
    result: dict = {"published": False, "candidates": 0, "reason": ""}

    raw = await load_all_feeds()
    result["raw"] = len(raw)

    sources_count: Dict[str, int] = {}
    for art in raw:
        sources_count[art.source] = sources_count.get(art.source, 0) + 1
    logger.info(f"📰 Источники: {sources_count}")
    logger.info(f"📡 Работающих источников: {sum(1 for v in sources_count.values() if v)}/{len(RSS_FEEDS)}")

    if shutdown_event.is_set():
        result["reason"] = "shutdown"
        return result

    candidates = filter_and_dedupe(raw, posted)
    result["candidates"] = len(candidates)
    if not candidates:
        logger.info("📭 Нет подходящих новостей. Завершаем работу.")
        result["reason"] = "no_candidates"
        return result

    ranked = select_candidates(candidates, posted)
    recent = posted.get_recent_posts(config.rotation_history_size)
    recent_formats = [p.get('format') or "" for p in recent]
    recent_topics = [p.get('topic') or "" for p in recent]

    extracted = 0
    for item in ranked[: config.max_generation_attempts]:
        if shutdown_event.is_set():
            logger.info("🛑 Прерывание в цикле публикации")
            result["reason"] = "shutdown"
            break

        article = item.article
        topic = Topic.detect(article.text)

        if _topic_in_row_blocks(topic, recent_topics):
            posted.log_rejected(article, f"TOPIC_IN_ROW ({topic})")
            continue

        assessment, extracted = await _evaluate_candidate(article, runtime.llm, extracted)
        score = assessment.total
        band = priority_band(score)
        logger.info(
            f"  🧮 ОЦЕНКА [{assessment.source}] {score}/100 ({band}), формат={assessment.post_format}, "
            f"триггер={assessment.trigger}: {article.title[:55]}"
        )

        if not assessment.worth_publishing:
            posted.log_rejected(article, f"NOT_WORTH_PUBLISHING ({assessment.reason or 'модель'})")
            continue

        if band in ("reject", "queue"):
            posted.log_rejected(article, f"LOW_SCORE ({score}/{band})")
            continue

        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            posted.log_rejected(article, f"FINAL_DUP: {'; '.join(dup_result.reasons[:2])}")
            continue

        post_format = choose_format(assessment, recent_formats)
        generated = await generate_post(article, assessment, post_format, runtime.llm)
        if not generated:
            posted.log_rejected(article, "GENERATION_FAILED")
            continue

        text = build_post_text(article, generated.body, post_format, topic)
        ok, reason = validate_post(generated.body, post_format)
        if not ok:
            posted.log_rejected(article, f"FINAL_VALIDATION ({reason})")
            continue

        published = await send_post(article, text, posted, runtime.bot, topic, post_format, score)
        if published:
            result.update({
                "published": True,
                "title": article.title,
                "score": score,
                "format": post_format,
                "trigger": assessment.trigger,
                "quality": generated.quality.as_dict(),
            })
            logger.info("🏁 Готово!")
            break

        posted.log_rejected(article, "SEND_FAILED")
        await asyncio.sleep(2)

    if not result["published"]:
        logger.info("😔 Не удалось опубликовать ни одну статью.")
        result["reason"] = result["reason"] or "nothing_published"
    return result


def _acquire_lock() -> bool:
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                logger.error(f"❌ Бот уже запущен (PID {old_pid})! Удалите {LOCK_FILE} если это ошибка.")
                return False
            logger.warning(f"⚠️ Устаревший lock (PID {old_pid} не существует), удаляю...")
            os.remove(LOCK_FILE)
        except Exception:
            logger.warning(f"⚠️ Не удалось прочитать {LOCK_FILE}, удаляю и продолжаю...")
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except OSError:
        pass


async def main() -> int:
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"🛑 Получен сигнал {signum}, завершаем...")
        shutdown_event.set()

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except ValueError:
        pass  # не главный поток (тесты)

    config.validate()
    if not _acquire_lock():
        return 1

    logger.info("=" * 60)
    logger.info("🚀 БЛОКИРОВКИ + AI (редакционная система, этап 2)")
    logger.info("=" * 60)

    runtime = Runtime()
    posted = None
    try:
        runtime.init()

        if not await check_telegram_connection(runtime.bot):
            logger.error("❌ Не удалось подключиться к Telegram. Проверьте токен и сеть.")
            return 1

        posted = PostedManager(config.db_file)
        if not posted.verify_db():
            logger.error("❌ Проблема с БД!")
            return 1
        logger.info("✅ БД OK")

        posted.cleanup(config.retention_days)
        stats = posted.get_stats()
        logger.info(f"📊 Статистика: {stats['total_posted']} posted, "
                    f"{stats['total_rejected']} отклонено (журнал причин)")

        recent = posted.get_recent_posts(config.rotation_history_size)
        if recent:
            logger.info(f"📋 Последние {len(recent)} постов:")
            for p in recent:
                logger.info(f"   • [{p['topic']}][{p.get('format') or '—'}][{p.get('source', '?')}] "
                            f"{p['title'][:50]}...")

        result = await run_cycle(runtime, posted, shutdown_event)
        logger.info(f"📈 Итог прогона: {result}")
        return 0 if result.get("published") or result.get("reason") in ("no_candidates", "shutdown") else 1

    except asyncio.CancelledError:
        logger.info("🛑 Операция отменена")
        return 1
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        return 1
    finally:
        if posted:
            posted.close()
        await runtime.close()
        _release_lock()
        logger.info("👋 Завершение работы")


__all__ = [
    "main", "run_cycle", "Runtime", "filter_and_dedupe", "select_candidates",
    "send_post", "check_telegram_connection", "PostFormat", "Assessment",
    "heuristic_assessment",
]
