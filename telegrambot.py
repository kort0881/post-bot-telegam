#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
import random
import re
import hashlib
import logging
import difflib
import sqlite3
import threading
import signal
import sys
from datetime import datetime, timezone
from typing import List, Set, Optional, Tuple, Dict
from urllib.parse import urlparse, parse_qs, urlencode
from dataclasses import dataclass, field
from functools import lru_cache
from collections import defaultdict, deque

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from groq import Groq

# ====================== ЛОГИ ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("block_ai_poster.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ====================== CONFIG ======================
class Config:
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.channel_id = os.getenv("CHANNEL_ID")
        self.retention_days = int(os.getenv("RETENTION_DAYS", "90"))
        self.rejected_retention_days = 30
        self.db_file = "posted_articles.db"

        self.title_similarity_threshold = 0.60
        self.ngram_similarity_threshold = 0.55
        self.entity_overlap_threshold = 0.60
        self.jaccard_threshold = 0.55
        self.same_domain_similarity = 0.65

        self.subject_window_hours = 24
        self.max_posts_per_subject = 5
        self.subject_min_interval_hours = 2
        self.same_subject_similarity_threshold = 0.60
        self.same_subject_cooldown_hours = 3

        self.alternation_enabled = True

        self.min_post_length = 500
        self.max_article_age_hours = 168
        self.min_ai_score = 1
        self.max_repeat_sentences = 2

        self.diversity_window = 8
        self.same_topic_limit = 3

        self.rotation_history_size = 10
        self.rotation_max_per_subject = 1
        self.rotation_max_per_source = 3
        self.min_subjects_between_repeats = 3

        self.source_min_posts_between = 1
        self.source_max_in_window = 4

        self.batch_subject_limit = 5

        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0
        self.telegram_timeout = 30
        self.http_timeout = 30

        missing = []
        for var, name in [(self.groq_api_key, "GROQ_API_KEY"),
                          (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                          (self.channel_id, "CHANNEL_ID")]:
            if not var:
                missing.append(name)
        if missing:
            raise SystemExit(f"❌ Отсутствуют: {', '.join(missing)}")


config = Config()

bot: Optional[Bot] = None
groq_client: Optional[Groq] = None

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def init_clients():
    global bot, groq_client
    try:
        bot = Bot(
            token=config.telegram_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logger.info("✅ Telegram Bot инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram Bot: {e}")
        raise
    try:
        groq_client = Groq(api_key=config.groq_api_key)
        logger.info("✅ Groq client инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Groq: {e}")
        raise


GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# ---------- RSS-фиды ----------
RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat AI"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica AI"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED AI"),
    ("https://the-decoder.com/feed/", "The Decoder"),
    ("https://9to5google.com/guides/google-ai/feed/", "9to5Google AI"),
    ("https://9to5mac.com/guides/apple-intelligence/feed/", "9to5Mac AI"),
    ("https://www.zdnet.com/topic/artificial-intelligence/rss.xml", "ZDNet AI"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review AI"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://engineering.fb.com/category/ml-applications/feed/", "Meta AI Blog"),
    ("https://kod.ru/rss", "Kod.ru"),
    (
        "https://habr.com/ru/rss/feed/1cf1798b4d67ac63d1869bba8f26920f"
        "?fl=ru&complexity=high&rating=10&types%5B%5D=article"
        "&types%5B%5D=post&types%5B%5D=news",
        "Habr AI"
    ),
    ("https://rkn.gov.ru/rss/news.xml", "РКН новости"),
    # t.me/s/* — HTML, не RSS; заменены на реальные RSS-источники
    ("https://roskomsvoboda.org/feed/", "Роскомсвобода"),
    ("https://opennet.me/rss/news", "OpenNet"),
    ("https://habr.com/ru/rss/hub/internet_regulation/all/", "Habr Регулирование"),
    ("https://www.comnews.ru/rss/news", "ComNews"),
]

# ---------- КЛЮЧЕВЫЕ СЛОВА ----------
AI_KEYWORDS_STRONG = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model",
    "chatgpt", "openai", "anthropic", "deepmind",
    "gpt-4", "gpt-5", "gpt-4o", "claude", "gemini",
    "midjourney", "dall-e", "stable diffusion", "sora",
    "deepseek", "mistral", "llama", "grok",
    "transformer", "diffusion model", "foundation model",
    "generative ai", "gen ai",
    "computer vision", "natural language processing",
    "reinforcement learning", "ai safety", "agi",
    "нейросеть", "нейросети", "искусственный интеллект",
    "машинное обучение", "генеративный ии",
]

AI_KEYWORDS_WEAK = [
    "ai", "nvidia", "copilot", "generative",
    "multimodal", "reasoning", "inference", "embedding",
    "robotics", "humanoid", "automation",
    "nlp", "ai model", "ai training",
    "бот", "боты", "автоматизация",
    "нейро", "ии",
]

BLOCK_KEYWORDS = [
    "блокировка", "заблокирован", "реестр ркн", "roskomnadzor", "rkn",
    "обход блокировок", "dpi", "замедление трафика", "sniffing",
    "vless", "v2ray", "xray", "wireguard", "openvpn", "amnezia",
    "белый список", "whitelist", "прокси", "туннелирование",
    "utls", "fragment", "антизапрет", "antizapret",
    "замедление youtube", "замедление ютуб",
]

GAMES_EXCLUDE = [
    "ps5", "xbox", "nintendo", "game review", "baldur's gate", "roblox", "esports",
    "twitch streamer", "fortnite", "игра", "игровая", "гейминг", "киберспорт"
]

BUSINESS_EXCLUDE = [
    "steps down", "resigns", "fired", "laid off", "layoffs", "new ceo", "new cto",
    "promoted to", "departing", "leaves company", "board meeting", "shareholder",
    "quarterly earnings", "earnings call", "revenue report", "stock price", "ipo",
    "merger", "acquisition", "lawsuit filed", "sued by", "legal battle",
    "уходит", "уволен", "увольнение", "сокращение", "назначен", "покидает",
    "совет директоров", "акционеры", "квартальный отчёт", "выручка", "капитализация",
    "слияние", "поглощение", "судебный иск"
]

PROMO_PATTERNS = [
    "newsletter", "рассылка", "подпишитесь", "subscribe", "sign up",
    "free trial", "скидка на подписку", "вебинар", "webinar", "buy now", "special offer",
    "купить vpn", "vpn сервис", "тариф", "промокод"
]


# ---------- DATACLASS ARTICLE ----------
@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------- ТОПИКИ ----------
class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    MESSENGER = "messenger"
    GENERAL = "general"
    BLOCK = "block"
    BYPASS = "bypass"
    WHITELIST = "whitelist"

    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #StableDiffusion #ИИАрт",
        ROBOTICS: "#роботы #робототехника #автоматизация",
        HARDWARE: "#NVIDIA #чипы #GPU",
        MESSENGER: "#Telegram #мессенджеры #боты",
        GENERAL: "#ИИ #технологии #AI",
        BLOCK: "#РКН #блокировки #цензура",
        BYPASS: "#VLESS #Xray #обход #VPN",
        WHITELIST: "#белыйсписок #доступность",
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["блокировк", "ркн", "roskomnadzor", "заблокирован", "реестр"]):
            return Topic.BLOCK
        if any(x in t for x in ["vless", "v2ray", "xray", "wireguard", "обход", "dpi", "антизапрет"]):
            return Topic.BYPASS
        if any(x in t for x in ["белый список", "whitelist", "доступность сайта"]):
            return Topic.WHITELIST
        if any(x in t for x in ["telegram", "телеграм", "мессенджер", "durov"]):
            return Topic.MESSENGER
        if any(x in t for x in ["gpt", "claude", "gemini", "llm", "chatgpt", "llama"]):
            return Topic.LLM
        if any(x in t for x in ["dall-e", "midjourney", "stable diffusion", "sora"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "chip", "gpu", "hardware"]):
            return Topic.HARDWARE
        return Topic.GENERAL


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def normalize_url(url: str) -> str:
    try:
        u = url.lower().strip()
        u = u.replace("https://", "").replace("http://", "")
        u = u.replace("www.", "")
        if "?" in u:
            base, query_str = u.split("?", 1)
            params = parse_qs(query_str)
            tracking = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
                        'fbclid', 'gclid', 'ref', 'source', 'mc_cid', 'mc_eid'}
            clean = {k: v for k, v in params.items() if k.lower() not in tracking}
            if clean:
                query = urlencode(clean, doseq=True)
                u = f"{base}?{query}"
            else:
                u = base
        u = u.rstrip("/")
        return u
    except Exception:
        return url.lower().strip().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def get_domain(url: str) -> str:
    try:
        u = url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
        return u.split("/")[0]
    except Exception:
        return ""


@lru_cache(maxsize=2000)
def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # Нормализуем версии продуктов (GPT-4o → gpt4o), но НЕ трогаем чистые числа/даты
    t = re.sub(
        r'([a-zA-Zа-яА-ЯёЁ]+)\s*[-.]?\s*(\d+(?:\.\d+)?)',
        lambda m: m.group(1) + m.group(2).replace('.', ''),
        t
    )
    return t


@lru_cache(maxsize=2000)
def get_title_words(title: str) -> frozenset:
    words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ0-9]+\b', title.lower())
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'to', 'of',
        'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
        'during', 'before', 'after', 'above', 'below', 'between', 'under',
        'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
        'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
        'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too',
        'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until', 'while',
        'about', 'against', 'its', 'new', 'says', 'said', 'get', 'got', 'gets',
        'make', 'made', 'makes', 'now', 'also', 'first', 'using', 'used', 'use',
        'out', 'up', 'what', 'which', 'who', 'this', 'that', 'these', 'those',
        'it', 'its', 'you', 'your', 'we', 'our', 'they', 'their', 'he', 'she',
        'him', 'her', 'his', 'hers', 'my', 'mine', 'yours', 'ours', 'theirs',
        'и', 'в', 'на', 'с', 'по', 'для', 'от', 'из', 'за', 'до', 'не',
        'что', 'как', 'это', 'все', 'его', 'она', 'они', 'мы', 'вы', 'он',
        'но', 'то', 'так', 'уже', 'или', 'ещё', 'еще', 'при', 'без',
        'тоже', 'также', 'будет', 'была', 'были', 'быть', 'может',
        'этот', 'эта', 'эти', 'тот', 'того', 'этого', 'свой', 'свои',
    }
    return frozenset(w for w in words if len(w) > 2 and w not in stop_words)


def get_sorted_word_signature(title: str) -> str:
    words = get_title_words(title)
    return ' '.join(sorted(words))


def calculate_similarity(str1: str, str2: str) -> float:
    return difflib.SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)


def ngram_similarity(str1: str, str2: str, n: int = 2) -> float:
    def get_ngrams(text: str, n: int) -> Set[str]:
        words = text.lower().split()
        if len(words) < n:
            return set(words)
        return set(' '.join(words[i:i + n]) for i in range(len(words) - n + 1))

    ng1 = get_ngrams(str1, n)
    ng2 = get_ngrams(str2, n)
    if not ng1 or not ng2:
        return 0.0
    return len(ng1 & ng2) / len(ng1 | ng2)


def get_content_hash(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())[:300]
    return hashlib.md5(normalized.encode()).hexdigest()


def parse_db_datetime(date_str: str) -> datetime:
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def safe_json_loads(value: str, default=None):
    if not value or value in ('null', 'None', '[]', '{}'):
        return default if default is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def ai_relevance_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for kw in AI_KEYWORDS_STRONG:
        if kw in text_lower:
            score += 2
    for kw in AI_KEYWORDS_WEAK:
        if kw in text_lower:
            score += 1
    if score == 0 and ("ai" in text_lower or "нейросеть" in text_lower or "ии" in text_lower):
        score = 1
    return score


def is_promo_content(text: str) -> bool:
    text_lower = text.lower()
    promo_count = sum(1 for p in PROMO_PATTERNS if p in text_lower)
    return promo_count >= 2


def is_relevant(article: Article) -> bool:
    text = f"{article.title} {article.summary}".lower()

    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        logger.info(f"  ⏰ TOO_OLD ({age_hours:.0f}h): {article.title[:50]}")
        return False

    if any(g in text for g in GAMES_EXCLUDE):
        logger.info(f"  🎮 GAME: {article.title[:50]}")
        return False

    if any(b in text for b in BUSINESS_EXCLUDE):
        logger.info(f"  🏢 BUSINESS: {article.title[:50]}")
        return False

    if is_promo_content(text):
        logger.info(f"  📢 PROMO: {article.title[:50]}")
        return False

    is_ai = (ai_relevance_score(text) >= config.min_ai_score or
             "ai" in text or "нейросеть" in text or "ии" in text)
    is_block = any(kw in text for kw in BLOCK_KEYWORDS)

    if not (is_ai or is_block):
        logger.info(f"  🚫 NEITHER AI NOR BLOCK: {article.title[:50]}")
        return False

    if is_block and any(ad in text for ad in ["купить", "скидка", "промокод", "тариф"]):
        logger.info(f"  🛑 VPN_AD: {article.title[:50]}")
        return False

    logger.info(f"  ✅ PASS (ai={is_ai}, block={is_block}): {article.title[:55]}")
    return True


@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    reasons: List[str]
    max_similarity: float = 0.0
    matched_title: str = ""

    def add_reason(self, reason: str, similarity: float = 0.0, matched: str = ""):
        self.reasons.append(reason)
        if similarity > self.max_similarity:
            self.max_similarity = similarity
            self.matched_title = matched
        self.is_duplicate = True


# ====================== POSTED MANAGER ======================
class PostedManager:
    def __init__(self, db_file: str = "posted_articles.db"):
        self.db_file = db_file
        # Отдельное соединение на каждый поток
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_file, timeout=30.0, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS posted_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    norm_url TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    title_normalized TEXT NOT NULL,
                    title_words TEXT,
                    title_word_signature TEXT,
                    summary TEXT,
                    content_hash TEXT,
                    entities TEXT,
                    topic TEXT DEFAULT 'general',
                    subject TEXT DEFAULT 'other',
                    source TEXT,
                    posted_date TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rejected_urls (
                    norm_url TEXT PRIMARY KEY,
                    title TEXT,
                    reason TEXT,
                    rejected_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Добавляем колонку subject если её нет (миграция)
            try:
                cursor.execute("ALTER TABLE posted_articles ADD COLUMN subject TEXT DEFAULT 'other'")
                conn.commit()
            except Exception:
                pass

            indices = [
                ('idx_norm_url', 'norm_url'),
                ('idx_content_hash', 'content_hash'),
                ('idx_domain', 'domain'),
                ('idx_posted_date', 'posted_date'),
                ('idx_title_normalized', 'title_normalized'),
                ('idx_title_word_signature', 'title_word_signature'),
                ('idx_subject', 'subject'),
            ]
            for idx_name, column in indices:
                try:
                    cursor.execute(
                        f'CREATE INDEX IF NOT EXISTS {idx_name} ON posted_articles({column})'
                    )
                except Exception:
                    pass
            conn.commit()
        logger.info("📚 База данных инициализирована")

    def _add_rejected(self, norm_url: str, title: str, reason: str):
        try:
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    'INSERT OR REPLACE INTO rejected_urls '
                    '(norm_url, title, reason, rejected_at) VALUES (?, ?, ?, datetime("now"))',
                    (norm_url, title[:200], reason)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Ошибка добавления в rejected: {e}")

    def is_rejected(self, url: str) -> Tuple[bool, str]:
        norm_url = normalize_url(url)
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute(
                'SELECT reason FROM rejected_urls WHERE norm_url = ? '
                'AND rejected_at > datetime("now", ?)',
                (norm_url, f'-{config.rejected_retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                return True, row[0]
            return False, ""

    def get_subject_posts_in_window(self, subject: str, hours: int) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, posted_date, title_normalized, entities
                FROM posted_articles
                WHERE subject = ?
                  AND posted_date > datetime('now', ?)
                ORDER BY posted_date DESC
            ''', (subject, f'-{hours} hours'))
            results = []
            for r in cursor.fetchall():
                results.append({
                    'title': r[0],
                    'date': r[1],
                    'normalized': r[2],
                    'entities': r[3]
                })
            return results

    def get_subject_stats_cached(self, hours: int = 24) -> Dict[str, List[dict]]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject, title, posted_date, title_normalized
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
                ORDER BY posted_date DESC
            ''', (f'-{hours} hours',))
            result: Dict[str, List[dict]] = defaultdict(list)
            for r in cursor.fetchall():
                result[r[0]].append({
                    'title': r[1],
                    'date': r[2],
                    'normalized': r[3]
                })
            return dict(result)

    def get_last_n_subjects(self, n: int = 5) -> List[str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject FROM posted_articles
                ORDER BY posted_date DESC
                LIMIT ?
            ''', (n,))
            return [row[0] for row in cursor.fetchall()]

    def can_post_subject(self, subject: str) -> Tuple[bool, str]:
        """Проверяет, не повторяется ли subject слишком часто подряд."""
        if subject == "other":
            return True, ""
        last_subjects = self.get_last_n_subjects(config.min_subjects_between_repeats)
        if subject in last_subjects:
            position = last_subjects.index(subject) + 1
            return (
                False,
                f"RECENT ({subject} был {position}-м из последних "
                f"{config.min_subjects_between_repeats})"
            )
        return True, ""

    def check_subject_limit(
        self,
        subject: str,
        new_title: str,
        new_entities: Set[str] = None
    ) -> Tuple[bool, str]:
        if subject == "other":
            return True, ""

        recent_posts = self.get_subject_posts_in_window(subject, config.subject_window_hours)

        if len(recent_posts) >= config.max_posts_per_subject:
            return (
                False,
                f"SUBJECT_LIMIT ({subject}: {len(recent_posts)}/"
                f"{config.max_posts_per_subject} за {config.subject_window_hours}h)"
            )

        if recent_posts:
            last_date = parse_db_datetime(recent_posts[0]['date'])
            hours_since = (datetime.now(timezone.utc) - last_date).total_seconds() / 3600
            if hours_since < config.subject_min_interval_hours:
                return (
                    False,
                    f"SUBJECT_COOLDOWN ({subject}: {hours_since:.1f}h "
                    f"< {config.subject_min_interval_hours}h)"
                )

        new_normalized = normalize_title(new_title)
        for post in recent_posts:
            sim = calculate_similarity(new_normalized, post['normalized'])
            if sim > config.same_subject_similarity_threshold:
                return False, f"SUBJECT_SIMILAR ({subject}, sim={sim:.0%})"

        return True, ""

    def is_duplicate(self, url: str, title: str, summary: str = "") -> DuplicateCheckResult:
        result = DuplicateCheckResult(is_duplicate=False, reasons=[])
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            norm_url = normalize_url(url)
            title_normalized = normalize_title(title)
            title_words = set(get_title_words(title))
            content_hash = get_content_hash(f"{title} {summary}")
            domain = get_domain(url)

            # 1. Чёрный список
            cursor.execute(
                'SELECT reason FROM rejected_urls WHERE norm_url = ? '
                'AND rejected_at > datetime("now", ?)',
                (norm_url, f'-{config.rejected_retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                result.add_reason(f"BLACKLISTED ({row[0]})", 1.0, title)
                return result

            # 2. URL точное совпадение
            cursor.execute(
                'SELECT title FROM posted_articles WHERE norm_url = ? '
                'AND posted_date > datetime("now", ?)',
                (norm_url, f'-{config.retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                result.add_reason("URL_EXACT", 1.0, row[0])
                return result

            # 3. Хэш контента
            if content_hash:
                cursor.execute(
                    'SELECT title FROM posted_articles WHERE content_hash = ? '
                    'AND posted_date > datetime("now", ?)',
                    (content_hash, f'-{config.retention_days} days')
                )
                row = cursor.fetchone()
                if row:
                    result.add_reason("CONTENT_HASH", 1.0, row[0])
                    return result

            # 4. Точный заголовок
            cursor.execute(
                'SELECT title FROM posted_articles WHERE title_normalized = ? '
                'AND posted_date > datetime("now", ?)',
                (title_normalized, f'-{config.retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                result.add_reason("TITLE_EXACT", 1.0, row[0])
                return result

            # 5. Нечёткие совпадения
            cursor.execute('''
                SELECT id, title, title_normalized, title_words, domain
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
            ''', (f'-{config.retention_days} days',))
            all_posts = cursor.fetchall()

            for row in all_posts:
                existing_id, existing_title, existing_normalized, existing_words_json, existing_domain = row

                seq_sim = calculate_similarity(title_normalized, existing_normalized or "")
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)

                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)

                existing_words = set(safe_json_loads(existing_words_json, []))
                if existing_words:
                    jaccard = jaccard_similarity(title_words, existing_words)
                    if jaccard > config.jaccard_threshold:
                        result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)

                if domain == existing_domain:
                    same_sim = calculate_similarity(title_normalized, existing_normalized or "")
                    if same_sim > config.same_domain_similarity:
                        result.add_reason(f"SAME_DOMAIN ({same_sim:.0%})", same_sim, existing_title)

            return result

    def check_diversity(self, topic: str, source: str = "") -> Tuple[bool, str]:
        with self._lock:
            cursor = self._get_conn().cursor()

            if source:
                cursor.execute(
                    'SELECT source FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                    (config.rotation_history_size,)
                )
                recent_sources = [row[0] for row in cursor.fetchall()]
                last_few = recent_sources[:config.source_min_posts_between]

                if source in last_few:
                    pos = last_few.index(source) + 1
                    return (
                        False,
                        f"SOURCE_TOO_RECENT ({source} был {pos}-м из последних "
                        f"{config.source_min_posts_between})"
                    )

                source_count = sum(1 for s in recent_sources if s == source)
                if source_count >= config.source_max_in_window:
                    return (
                        False,
                        f"SOURCE_LIMIT ({source}: {source_count}/{config.source_max_in_window} "
                        f"за последние {config.rotation_history_size})"
                    )

            if topic == Topic.GENERAL:
                return True, ""

            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,)
            )
            recent_topics = [row[0] for row in cursor.fetchall()]
            if not recent_topics:
                return True, ""

            same_count = sum(1 for t in recent_topics if t == topic)
            if same_count >= config.same_topic_limit:
                return False, f"TOO_MANY: {same_count}/{config.diversity_window} = {topic}"

            return True, ""

    def add(self, article: Article, topic: str = Topic.GENERAL, subject: str = "other") -> bool:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            norm_url = normalize_url(article.link)
            domain_val = get_domain(article.link)
            title_normalized = normalize_title(article.title)
            title_words = list(get_title_words(article.title))
            word_signature = get_sorted_word_signature(article.title)
            content_hash = get_content_hash(f"{article.title} {article.summary}")
            try:
                cursor.execute('''
                    INSERT INTO posted_articles
                    (url, norm_url, domain, title, title_normalized, title_words,
                     title_word_signature, summary, content_hash, entities, topic, subject, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.link, norm_url, domain_val, article.title, title_normalized,
                    json.dumps(title_words), word_signature, article.summary[:1000],
                    content_hash, json.dumps([]), topic, subject, article.source
                ))
                conn.commit()
                cursor.execute('SELECT id FROM posted_articles WHERE norm_url = ?', (norm_url,))
                saved = cursor.fetchone()
                if saved:
                    logger.info(f"💾 Сохранено (ID={saved[0]}, topic={topic}): {article.title[:50]}...")
                    return True
                else:
                    logger.error(f"❌ Не сохранено: {article.title[:50]}")
                    return False
            except sqlite3.IntegrityError:
                logger.warning(f"⚠️ Уже существует: {article.title[:40]}")
                return False
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения: {e}")
                return False

    def log_rejected(self, article: Article, reason: str):
        logger.info(f"🚫 [{reason}]: {article.title[:50]}")
        norm_url = normalize_url(article.link)
        self._add_rejected(norm_url, article.title, reason)

    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, topic, source, posted_date, subject
                FROM posted_articles
                ORDER BY posted_date DESC
                LIMIT ?
            ''', (limit,))
            results = []
            for r in cursor.fetchall():
                results.append({
                    'title': r[0], 'topic': r[1], 'source': r[2],
                    'date': r[3], 'subject': r[4] if len(r) > 4 else 'other'
                })
            return results

    def get_last_topic(self) -> Optional[str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT 1')
            row = cursor.fetchone()
            return row[0] if row else None

    def get_recent_subjects(self, hours: int = 24) -> Dict[str, int]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject, COUNT(*) as cnt
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
                GROUP BY subject
                ORDER BY cnt DESC
            ''', (f'-{hours} hours',))
            return {row[0]: row[1] for row in cursor.fetchall()}

    def cleanup(self, days: int = 90):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM posted_articles WHERE posted_date < datetime('now', '-{days} days')"
            )
            deleted_posted = cursor.rowcount
            cursor.execute(
                f"DELETE FROM rejected_urls "
                f"WHERE rejected_at < datetime('now', '-{config.rejected_retention_days} days')"
            )
            deleted_rejected = cursor.rowcount
            conn.commit()
            if deleted_posted > 0 or deleted_rejected > 0:
                logger.info(f"🧹 Очищено: {deleted_posted} posted, {deleted_rejected} rejected")

    def get_stats(self) -> dict:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT COUNT(*) FROM posted_articles')
            total = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM rejected_urls')
            rejected = cursor.fetchone()[0]
            return {'total_posted': total, 'total_rejected': rejected}

    def verify_db(self) -> bool:
        with self._lock:
            try:
                cursor = self._get_conn().cursor()
                cursor.execute('PRAGMA integrity_check')
                return cursor.fetchone()[0] == 'ok'
            except Exception:
                return False

    def close(self):
        with self._lock:
            conn = getattr(self._local, 'conn', None)
            if conn:
                try:
                    conn.commit()
                    conn.close()
                    logger.info("🔒 БД закрыта")
                except Exception as e:
                    logger.error(f"❌ Ошибка закрытия БД: {e}")
                finally:
                    self._local.conn = None


# ====================== RSS LOADING ======================
async def fetch_feed(url: str, source: str) -> List[Article]:
    try:
        await asyncio.sleep(random.uniform(0.3, 1.5))
        timeout = aiohttp.ClientTimeout(total=config.http_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=HEADERS) as resp:
                if resp.status != 200:
                    logger.warning(f"  ⚠️ {source}: HTTP {resp.status}")
                    return []
                content = await resp.text()
        feed = await asyncio.to_thread(feedparser.parse, content)
        articles = []
        for entry in feed.entries[:20]:
            link = entry.get('link', '').strip()
            title = entry.get('title', '').strip()
            summary = re.sub(
                r'<[^>]+>', '',
                entry.get('summary', entry.get('description', '')).strip()
            )
            if not link or not title or len(title) < 15:
                continue
            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = (
                datetime(*pub_date[:6], tzinfo=timezone.utc)
                if pub_date
                else datetime.now(timezone.utc)
            )
            articles.append(Article(
                title=title, summary=summary, link=link,
                source=source, published=published
            ))
        logger.info(f"  ✅ {source}: {len(articles)}")
        return articles
    except asyncio.TimeoutError:
        logger.warning(f"  ⚠️ {source}: Timeout")
        return []
    except Exception as e:
        logger.warning(f"  ⚠️ {source}: {e}")
        return []


async def load_all_feeds() -> List[Article]:
    logger.info("📥 Загрузка RSS...")
    tasks = [fetch_feed(url, source) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"  ⚠️ {RSS_FEEDS[i][1]}: {result}")
        elif result:
            all_articles.extend(result)
    logger.info(f"📦 Всего: {len(all_articles)}")
    return all_articles


def interleave_by_source(candidates: List[Article]) -> List[Article]:
    """Чередует статьи из разных источников round-robin."""
    if not candidates:
        return []
    by_source: Dict[str, deque] = {}
    source_order: List[str] = []
    for art in candidates:
        if art.source not in by_source:
            by_source[art.source] = deque()
            source_order.append(art.source)
        by_source[art.source].append(art)
    result = []
    while any(by_source[s] for s in source_order):
        for source in source_order:
            if by_source[source]:
                result.append(by_source[source].popleft())
    return result


def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("🔍 Фильтрация...")
    logger.info(f"   Входящих статей: {len(articles)}")

    candidates = []
    seen_normalized_titles: Set[str] = set()
    seen_word_signatures: Set[str] = set()
    seen_content_hashes: Set[str] = set()
    batch_subject_counts: Dict[str, int] = defaultdict(int)

    stats = {
        "batch_dup": 0, "db_dup": 0, "diversity": 0, "passed": 0,
        "filtered_out": 0, "subject_limit": 0, "subject_rotation": 0,
        "batch_subject": 0, "blacklisted": 0,
    }

    for article in articles:
        is_blacklisted, bl_reason = posted.is_rejected(article.link)
        if is_blacklisted:
            logger.info(f"  ⛔ BLACKLISTED ({bl_reason}): {article.title[:50]}")
            stats["blacklisted"] += 1
            continue

        if not is_relevant(article):
            stats["filtered_out"] += 1
            continue

        title_normalized = normalize_title(article.title)
        if title_normalized in seen_normalized_titles:
            stats["batch_dup"] += 1
            continue

        word_sig = get_sorted_word_signature(article.title)
        if word_sig in seen_word_signatures:
            stats["batch_dup"] += 1
            continue

        content_hash = get_content_hash(f"{article.title} {article.summary}")
        if content_hash in seen_content_hashes:
            stats["batch_dup"] += 1
            continue

        text = f"{article.title} {article.summary}"
        subject = Topic.detect(text)

        # Проверка: subject слишком часто повторяется в хвосте истории
        subj_rot_ok, subj_rot_reason = posted.can_post_subject(subject)
        if not subj_rot_ok:
            posted.log_rejected(article, subj_rot_reason)
            stats["subject_rotation"] += 1
            continue

        # Лимит subject за один запуск
        if subject != "other" and batch_subject_counts[subject] >= config.batch_subject_limit:
            posted.log_rejected(
                article,
                f"BATCH_SUBJECT_LIMIT ({subject}, {batch_subject_counts[subject]} in batch)"
            )
            stats["batch_subject"] += 1
            continue

        # Лимит subject за окно времени + cooldown + схожесть внутри subject
        subj_ok, subj_reason = posted.check_subject_limit(subject, article.title)
        if not subj_ok:
            posted.log_rejected(article, subj_reason)
            stats["subject_limit"] += 1
            continue

        # Дедупликация по БД
        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            reason = "; ".join(dup_result.reasons[:3])
            posted.log_rejected(article, reason)
            stats["db_dup"] += 1
            continue

        # Разнообразие топиков
        topic = subject  # topic == subject (оба используют Topic.detect)
        div_ok, div_reason = posted.check_diversity(topic, article.source)
        if not div_ok:
            posted.log_rejected(article, div_reason)
            stats["diversity"] += 1
            continue

        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)

        batch_subject_counts[subject] += 1
        candidates.append(article)
        stats["passed"] += 1

    # Чередование AI ↔ блокировки
    last_topic = posted.get_last_topic()
    if config.alternation_enabled and last_topic:
        ai_topics = {Topic.LLM, Topic.IMAGE_GEN, Topic.ROBOTICS, Topic.HARDWARE,
                     Topic.MESSENGER, Topic.GENERAL}
        block_topics = {Topic.BLOCK, Topic.BYPASS, Topic.WHITELIST}
        preferred_group = block_topics if last_topic in ai_topics else ai_topics

        def alternation_score(art: Article) -> int:
            art_topic = Topic.detect(f"{art.title} {art.summary}")
            return 1 if art_topic in preferred_group else 0

        candidates.sort(key=alternation_score, reverse=True)
    else:
        def simple_score(art: Article) -> float:
            t = f"{art.title} {art.summary}"
            return ai_relevance_score(t) + (10 if any(kw in t for kw in BLOCK_KEYWORDS) else 0)

        candidates.sort(key=simple_score, reverse=True)

    candidates = interleave_by_source(candidates)

    logger.info("📊 Итоги фильтрации:")
    logger.info(
        f"   filtered={stats['filtered_out']}, batch_dup={stats['batch_dup']}, "
        f"db_dup={stats['db_dup']}, diversity={stats['diversity']}"
    )
    logger.info(
        f"   subject_limit={stats['subject_limit']}, subject_rotation={stats['subject_rotation']}, "
        f"batch_subject={stats['batch_subject']}, blacklisted={stats['blacklisted']}"
    )
    logger.info(f"✅ Кандидатов: {len(candidates)} из {len(articles)}")

    return candidates


def rotate_candidates(candidates: List[Article], posted: PostedManager) -> List[Article]:
    """Фильтрует кандидатов по истории источников, заблокированные добавляет в конец."""
    recent = posted.get_recent_posts(config.rotation_history_size)
    if not recent:
        return candidates

    recent_sources = [p.get('source', '') for p in recent]
    source_counts: Dict[str, int] = {}
    for src in recent_sources:
        source_counts[src] = source_counts.get(src, 0) + 1
    last_n_sources = recent_sources[:config.source_min_posts_between]

    priority: List[Article] = []
    deprioritized: List[Article] = []

    for art in candidates:
        src = art.source
        if src in last_n_sources:
            pos = last_n_sources.index(src) + 1
            logger.info(
                f"   ⬇️ DEPRIO [src {src}] был {pos}-м из последних "
                f"{config.source_min_posts_between}: {art.title[:40]}"
            )
            deprioritized.append(art)
        elif source_counts.get(src, 0) >= config.source_max_in_window:
            logger.info(
                f"   ⬇️ DEPRIO [src {src}] x{source_counts[src]} в истории: {art.title[:40]}"
            )
            deprioritized.append(art)
        else:
            priority.append(art)

    result = priority + deprioritized
    return result if result else candidates[:1]


DISCLAIMER = (
    "\n\n⚠️ Отдельные организации, упомянутые в данном материале, могут иметь статус "
    "«нежелательных» на территории РФ. Актуальный перечень размещён на официальном сайте "
    'Минюста РФ: <a href="https://minjust.gov.ru/ru/pages/perechen-inostrannyh-i-'
    'mezhdunarodnyh-organizacij-deyatelnost-kotoryh-priznana-nezhelatelnoj-na-territorii-'
    'rossiyskoy-federacii/">minjust.gov.ru</a>'
)


def has_repeated_sentences(text: str, max_repeats: int = 2) -> bool:
    sentences = re.split(r'[.!?]\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if len(sentences) < 2:
        return False
    repeat_count = 0
    checked = []
    for sent in sentences:
        for prev in checked:
            sim = calculate_similarity(sent, prev)
            if sim > 0.6:
                repeat_count += 1
                if repeat_count >= max_repeats:
                    return True
        checked.append(sent)
    return False


async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация: {article.title[:55]}...")
    text_for_topic = f"{article.title} {article.summary}"
    topic = Topic.detect(text_for_topic)
    is_block_topic = topic in (Topic.BLOCK, Topic.BYPASS, Topic.WHITELIST)

    if is_block_topic:
        prompt = f"""Ты — редактор канала про обход блокировок и цифровые свободы.

НОВОСТЬ:
Заголовок: {article.title}
Содержание: {article.summary[:1200]}
Источник: {article.source}

Напиши Telegram-пост по схеме:
1. Что именно заблокировали/замедлили (если новость о блокировке) или какой метод обхода появился.
2. Техническая причина (DPI, SNI, IP-блокировка).
3. Конкретная инструкция: как обойти с помощью VLESS / Xray / WireGuard (приведи пример фрагмента конфига или команды).
4. Где взять актуальные белые списки или прокси.

ТРЕБОВАНИЯ:
✅ 600-800 символов, живой язык, с эмодзи.
✅ Конкретные цифры, названия, команды.
❌ НИКАКИХ призывов поставить реакцию (огонь, палец вверх, сердечко), написать комментарий, подписаться, купить VPN.
❌ Без рекламы платных сервисов.

ПОСТ:"""
    else:
        prompt = f"""Ты — редактор Telegram-канала про AI-технологии для аудитории из РФ и СНГ.

НОВОСТЬ:
Заголовок: {article.title}
Содержание: {article.summary[:1200]}
Источник: {article.source}

Если новость НЕ про AI/нейросети — ответь одним словом: SKIP

Напиши Telegram-пост по схеме:
1. Главный факт с конкретными деталями (что случилось, кто, когда, цифры)
2. Как это работает или что изменилось — раскрой суть, не скупись на детали
3. Что это означает на практике — для пользователей, индустрии или конкурентов

ТРЕБОВАНИЯ:
✅ 700-1000 символов — пиши подробно, используй все важные детали из новости
✅ Конкретные цифры, названия моделей, даты, имена — это важно
✅ Живой разговорный стиль, без шаблонов и канцелярита
✅ Каждый блок — отдельный абзац
✅ Заканчивай уверенным утверждением или выводом — без вопросов читателю
❌ ЗАПРЕЩЕНО: вопросы в конце ("Что думаете?", "Как вам?" и подобное)
❌ ЗАПРЕЩЕНО: "стоит отметить", "важно понимать", "это меняет", "открывает возможности", \
"почему это важно", "важно отметить", "стоит упомянуть", "следует сказать", нумерация (1. 2. 3.)
❌ ЗАПРЕЩЕНО ЛЮБЫЕ ПРИЗЫВЫ: поставить реакцию (огонь, палец вверх, сердечко), \
написать комментарий, подписаться, переслать пост.

ПОСТ:"""

    water_phrases = [
        "стоит отметить", "важно понимать", "интересно, что",
        "давайте разберёмся", "как мы знаем", "не секрет",
        "нельзя не отметить", "следует подчеркнуть",
        "почему это важно", "для чего это важно",
        "это важно потому что", "это меняет всё",
        "это открывает возможности", "это меняет правила",
        "почему это меняет", "вот почему это важно",
        "важно отметить", "стоит упомянуть", "следует сказать",
    ]

    ending_question_patterns = [
        r'[Чч]то думаете', r'[Кк]ак вам', r'[Кк]аков ваш', r'[Вв]аше мнение',
        r'[Пп]оделитесь', r'[Жж]дём ваших', r'[Нн]апишите в комментар',
        r'[Аа] вы', r'\?$', r'\?\s*$',
    ]

    for model in GROQ_MODELS:
        for attempt in range(config.groq_retries_per_model):
            try:
                await asyncio.sleep(1)
                logger.info(f"  🤖 {model} (попытка {attempt + 1})")

                resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model,
                    temperature=0.9,
                    max_tokens=800,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.choices[0].message.content.strip()

                if "SKIP" in text.upper()[:10]:
                    logger.info("  ⏭️ SKIP (не подходит)")
                    return None

                if len(text) < config.min_post_length:
                    logger.warning(f"  ⚠️ Короткий ({len(text)} симв.), следующая модель...")
                    # break — выходим из попыток для этой модели, переходим к следующей
                    break

                if any(w in text.lower() for w in water_phrases):
                    logger.warning("  ⚠️ Обнаружена «вода», следующая модель...")
                    # break — та же температура/промпт не дадут другого результата
                    break

                last_paragraph = text.strip().split('\n')[-1].strip()
                has_ending_question = any(
                    re.search(p, last_paragraph) for p in ending_question_patterns
                )
                if has_ending_question:
                    logger.warning("  ⚠️ Вопрос в конце — удаляем последний абзац...")
                    paragraphs = [p.strip() for p in text.strip().split('\n') if p.strip()]
                    if len(paragraphs) > 1:
                        text = '\n\n'.join(paragraphs[:-1])
                    else:
                        text = re.sub(r'[\.\s]*[А-Яа-яA-Za-z\s,]+\?[\s]*$', '.', text).strip()

                if has_repeated_sentences(text, config.max_repeat_sentences):
                    logger.warning("  ⚠️ Повторяющиеся предложения, следующая модель...")
                    break

                hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])
                source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
                final = f"{text}\n\n{hashtags}{source_link}{DISCLAIMER}"

                logger.info(f"  ✅ [{model}]: {len(text)} симв.")
                return final

            except Exception as e:
                error_str = str(e).lower()
                if any(x in error_str for x in ["decommissioned", "deprecated", "not found"]):
                    logger.warning(f"  ⚠️ {model} недоступна, пропускаем")
                    break
                logger.error(f"  ❌ {model} попытка {attempt + 1}: {e}")
                await asyncio.sleep(config.groq_base_delay * (2 ** attempt))

    logger.error("  ❌ Все модели не сработали")
    return None


async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    """
    Сначала отправляем в Telegram, затем фиксируем в БД.
    Если отправка упала — статья не попадает в БД и может быть повторно обработана.
    """
    topic = Topic.detect(f"{article.title} {article.summary}")
    subject = topic

    try:
        logger.info("  📤 Отправка поста...")
        await bot.send_message(
            config.channel_id,
            text,
            disable_web_page_preview=False
        )
        logger.info(f"✅ ОПУБЛИКОВАНО [{topic}][{article.source}]: {article.title[:50]}")
    except Exception as e:
        logger.error(f"❌ Telegram ошибка отправки: {e}")
        return False

    # Сохраняем только после успешной отправки
    saved = posted.add(article, topic, subject)
    if not saved:
        # Статья отправлена, но не сохранена — логируем, чтобы не потерять
        logger.warning(
            f"⚠️ Пост отправлен, но не сохранён в БД (возможно дубль): {article.title[:50]}"
        )
    return True


async def check_telegram_connection() -> bool:
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


async def main():
    # Event создаётся внутри event loop — корректно для Python 3.10+
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"🛑 Получен сигнал {signum}, завершаем...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    lock_file = "bot.lock"

    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                logger.error(
                    f"❌ Бот уже запущен (PID {old_pid})! "
                    f"Удалите bot.lock если это ошибка."
                )
                return
            else:
                logger.warning(
                    f"⚠️ Найден устаревший lock (PID {old_pid} не существует), удаляю..."
                )
                os.remove(lock_file)
        except Exception:
            logger.warning("⚠️ Не удалось прочитать bot.lock, удаляю и продолжаю...")
            os.remove(lock_file)

    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))

    logger.info("=" * 60)
    logger.info("🚀 БЛОКИРОВКИ + AI (без игр/бизнеса/рекламы, без призывов реакций)")
    logger.info("=" * 60)

    posted = None

    try:
        init_clients()

        if not await check_telegram_connection():
            logger.error("❌ Не удалось подключиться к Telegram. Проверьте токен и сеть.")
            return

        posted = PostedManager(config.db_file)

        if posted.verify_db():
            logger.info("✅ БД OK")
        else:
            logger.error("❌ Проблема с БД!")
            return

        posted.cleanup(config.retention_days)

        stats = posted.get_stats()
        logger.info(
            f"📊 Статистика: {stats['total_posted']} posted, "
            f"{stats['total_rejected']} в чёрном списке"
        )

        recent = posted.get_recent_posts(config.rotation_history_size)
        if recent:
            logger.info(f"📋 Последние {len(recent)} постов:")
            for p in recent:
                logger.info(f"   • [{p['topic']}][{p.get('source', '?')}] {p['title'][:50]}...")

        if shutdown_event.is_set():
            logger.info("🛑 Прерывание перед загрузкой RSS")
            return

        raw = await load_all_feeds()

        sources_count: Dict[str, int] = {}
        for art in raw:
            sources_count[art.source] = sources_count.get(art.source, 0) + 1
        logger.info(f"📰 Источники: {sources_count}")

        working = sum(1 for v in sources_count.values() if v > 0)
        logger.info(f"📡 Работающих источников: {working}/{len(RSS_FEEDS)}")

        if shutdown_event.is_set():
            logger.info("🛑 Прерывание перед фильтрацией")
            return

        candidates = filter_and_dedupe(raw, posted)

        if not candidates:
            logger.info("📭 Нет подходящих новостей")
            return

        candidates = rotate_candidates(candidates, posted)

        logger.info("🎯 Топ-10 кандидатов после ротации:")
        for i, c in enumerate(candidates[:10]):
            topic = Topic.detect(f"{c.title} {c.summary}")
            logger.info(f"  {i + 1}. [{topic}] [{c.source}] {c.title[:55]}")

        published = False
        for article in candidates[:25]:
            if shutdown_event.is_set():
                logger.info("🛑 Прерывание в цикле публикации")
                break

            # Финальная проверка дубля (за время обработки кто-то мог добавить)
            dup_result = posted.is_duplicate(article.link, article.title, article.summary)
            if dup_result.is_duplicate:
                posted.log_rejected(
                    article,
                    f"FINAL_DUP: {'; '.join(dup_result.reasons[:2])}"
                )
                continue

            summary = await generate_summary(article)
            if not summary:
                posted.log_rejected(article, "GENERATION_FAILED")
                continue

            if await post_article(article, summary, posted):
                logger.info("🏁 Готово!")
                published = True
                break

            await asyncio.sleep(2)

        if not published:
            logger.info("😔 Не удалось опубликовать (все пропущены или ошибки генерации)")

    except asyncio.CancelledError:
        logger.info("🛑 Операция отменена")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
    finally:
        if posted:
            posted.close()
        if bot:
            try:
                await bot.session.close()
                logger.info("🔒 Telegram сессия закрыта")
            except Exception as e:
                logger.error(f"❌ Ошибка закрытия Telegram: {e}")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        logger.info("👋 Завершение работы")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Прервано пользователем")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)





































































































































































































































































