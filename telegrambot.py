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
from datetime import datetime, timezone
from typing import List, Set, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
from dataclasses import dataclass, field
from functools import lru_cache

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
        logging.FileHandler("ai_poster.log", encoding="utf-8"),
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
        self.db_file = "posted_articles.db"

        # Пороги дедупликации
        self.title_similarity_threshold = 0.55
        self.ngram_similarity_threshold = 0.40
        self.entity_overlap_threshold = 0.45
        self.jaccard_threshold = 0.50
        self.same_domain_similarity = 0.40

        # Длина поста
        self.min_post_length = 450
        self.max_article_age_hours = 48

        # Разнообразие
        self.diversity_window = 5
        self.same_topic_limit = 2

        # Groq
        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0

        # Rejected хранить не больше N дней
        self.rejected_retention_days = 7

        missing = []
        for var, name in [(self.groq_api_key, "GROQ_API_KEY"),
                          (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                          (self.channel_id, "CHANNEL_ID")]:
            if not var:
                missing.append(name)
        if missing:
            raise SystemExit(f"❌ Отсутствуют: {', '.join(missing)}")


config = Config()

bot = Bot(token=config.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
groq_client = Groq(api_key=config.groq_api_key)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ====================== GROQ МОДЕЛИ ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


# ====================== RSS ======================
RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat AI"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review AI"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica AI"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED AI"),
    ("https://www.artificialintelligence-news.com/feed/", "AI News"),
    ("https://openai.com/blog/rss/", "OpenAI Blog"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://www.marktechpost.com/feed/", "MarkTechPost"),
    ("https://kod.ru/feed/", "Kod.ru"),
]


# ====================== KEYWORDS ======================
AI_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model", "gpt", "chatgpt", "claude",
    "gemini", "grok", "llama", "mistral", "deepseek", "midjourney",
    "dall-e", "stable diffusion", "sora", "openai", "anthropic",
    "deepmind", "nvidia", "agi", "transformer", "generative",
    "computer vision", "nlp", "natural language processing", "diffusion model",
    "text-to-image", "text-to-video", "copilot", "ai model", "ai training",
    "reinforcement learning", "supervised learning", "unsupervised learning",
    "foundation model", "multimodal", "reasoning", "fine-tuning",
    "rlhf", "rag", "vector database", "embedding", "inference",
    "ai safety", "alignment", "robotics", "humanoid",
    "hugging face", "stability ai", "cohere", "perplexity",
    "inflection", "xai",
]

# Жёсткий exclude — ТОЛЬКО то, что НИКОГДА не связано с AI
HARD_EXCLUDE_KEYWORDS = [
    # Крипто
    "bitcoin", "crypto", "blockchain", "nft", "ethereum", "cryptocurrency",
    "web3", "defi", "token sale", "mining rig",

    # Чистый гейминг
    "ps5", "xbox", "nintendo", "game review", "baldur's gate",
    "roblox", "esports", "twitch streamer", "fortnite",

    # Развлечения
    "box office", "movie review", "tv show review", "hbo series",
    "netflix series", "celebrity gossip", "trailer release",
    "reality show", "award ceremony",

    # Спорт
    "nfl", "nba", "mlb", "nhl", "fifa", "olympics",
    "championship game", "player trade", "sports betting",
    "touchdown", "slam dunk",

    # Реклама
    "sponsored content", "partner content", "advertisement",
    "black friday deal", "deal alert", "promo code", "coupon",
]

# Мягкий exclude — блокируется ТОЛЬКО если нет AI-контекста
SOFT_EXCLUDE_KEYWORDS = [
    # Чистая макроэкономика
    "federal reserve", "fed rate", "interest rate cut", "interest rate hike",
    "recession fears", "gdp growth", "unemployment rate", "jobs report nonfarm",
    "consumer spending index", "housing market crash",
    "forex trading", "commodities futures", "oil price barrel",
    "bond yields", "treasury yields",

    # Чистая внутренняя политика
    "election results", "campaign trail", "voter turnout",
    "campaign donation", "primary election", "midterm election",
    "gun control debate", "mass shooting",
    "immigration reform bill", "border wall",
    "supreme court ruling",
]

BAD_PHRASES = [
    "sponsored", "partner content", "advertisement",
    "black friday", "deal alert", "promo code",
]


# ====================== KEY ENTITIES ======================
KEY_ENTITIES = [
    "openai", "google", "meta", "microsoft", "anthropic", "nvidia", "apple",
    "amazon", "deepmind", "hugging face", "stability ai", "midjourney",
    "mistral", "cohere", "perplexity", "xai", "inflection",
    "baidu", "alibaba", "tencent", "yandex", "sber",
    "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "claude 3", "claude 3.5",
    "gemini", "gemini 2", "llama", "llama 3", "mistral", "mixtral",
    "copilot", "dall-e", "sora", "stable diffusion", "flux", "grok",
    "deepseek", "qwen", "o1", "o3", "gigachat", "yandexgpt",
    "transformer", "diffusion", "multimodal", "reasoning", "fine-tuning",
    "rlhf", "rag", "vector database", "embedding", "inference",
    "agi", "asi", "ai safety", "alignment", "robotics", "humanoid",
]


# ====================== DATACLASS ======================
@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ====================== TOPIC ======================
class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    GENERAL = "general"

    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #StableDiffusion #ИИАрт",
        ROBOTICS: "#роботы #робототехника #автоматизация",
        HARDWARE: "#NVIDIA #чипы #GPU",
        GENERAL: "#ИИ #технологии #AI"
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["gpt", "claude", "gemini", "llm", "chatgpt", "llama"]):
            return Topic.LLM
        if any(x in t for x in ["dall-e", "midjourney", "stable diffusion", "sora", "image generat"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "chip", "gpu", "hardware", "tpu"]):
            return Topic.HARDWARE
        return Topic.GENERAL


# ====================== UTILITIES ======================
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


@lru_cache(maxsize=1000)
def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'(\w+)\s*[-.]?\s*(\d+(?:\.\d+)?)',
               lambda m: m.group(1) + m.group(2).replace('.', ''), t)
    return t


@lru_cache(maxsize=1000)
def get_title_words(title: str) -> frozenset:
    words = re.findall(r'\b[a-zA-Z0-9]+\b', title.lower())
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
        'him', 'her', 'his', 'hers', 'my', 'mine', 'yours', 'ours', 'theirs'
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
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


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

    intersection = len(ng1 & ng2)
    union = len(ng1 | ng2)
    return intersection / union if union > 0 else 0.0


def extract_entities(text: str) -> Set[str]:
    text_normalized = normalize_title(text)
    found = set()

    for entity in KEY_ENTITIES:
        entity_normalized = normalize_title(entity)
        if entity_normalized in text_normalized:
            found.add(entity_normalized)

    return found


def get_content_hash(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())[:300]
    return hashlib.md5(normalized.encode()).hexdigest()


# ====================== AI STRENGTH SCORE ======================
def ai_relevance_score(text: str) -> int:
    """Считает сколько AI-ключевых слов в тексте. Чем больше — тем сильнее AI-контент."""
    text_lower = text.lower()
    score = 0
    for kw in AI_KEYWORDS:
        if kw in text_lower:
            score += 1
    return score


# ====================== ФИЛЬТРЫ ======================
def is_economics_news(text: str) -> bool:
    """Чистая экономика БЕЗ AI контекста."""
    text_lower = text.lower()

    # Если есть сильный AI-контекст — НЕ экономика
    if ai_relevance_score(text_lower) >= 2:
        return False

    econ_keywords = [
        "inflation rate", "interest rate", "federal reserve", "fed rate",
        "recession", "gdp", "unemployment rate", "jobs report",
        "economic growth", "tariff war", "trade deficit",
        "stock market crash", "bond yields", "treasury",
        "fiscal policy", "monetary policy", "budget deficit",
        "central bank", "forex", "commodities", "oil price",
        "consumer spending", "retail sales", "housing market",
    ]

    econ_count = sum(1 for kw in econ_keywords if kw in text_lower)

    if econ_count >= 2:
        logger.info(f"    💵 PURE_ECONOMICS: {econ_count} терминов")
        return True

    return False


def is_local_us_news(text: str) -> bool:
    """Чисто внутренние американские новости без AI-значимости."""
    text_lower = text.lower()

    # AI-related government/policy — ВСЕГДА пропускаем
    ai_policy_phrases = [
        "ai regulation", "ai safety", "ai policy", "ai executive order",
        "ai legislation", "artificial intelligence act", "ai governance",
        "ai oversight", "ai bill", "regulate ai", "ai standards",
        "ai ethics", "responsible ai", "ai framework", "ai law",
        "ai ban", "ai restrict", "ai require", "ai compliance",
        "tech regulation", "tech policy",
    ]
    if any(phrase in text_lower for phrase in ai_policy_phrases):
        return False

    # Если есть AI ключевые слова — это не «локальная» новость
    if ai_relevance_score(text_lower) >= 2:
        return False

    # Глобальные маркеры — не локальная новость
    global_markers = [
        "global", "worldwide", "international", "launch", "release",
        "announce", "research", "open source", "api", "platform",
        "model", "technology", "startup", "funding",
    ]
    if any(marker in text_lower for marker in global_markers):
        return False

    # Чисто внутренние US дела
    us_internal = [
        "fbi investigation", "cia operation", "homeland security alert",
        "us military operation", "border patrol", "ice raid",
        "state legislature", "city council vote", "local police",
        "district attorney", "county sheriff", "school board",
        "voter registration", "ballot measure", "gerrymandering",
        "town hall meeting", "state governor",
    ]

    for kw in us_internal:
        if kw in text_lower:
            logger.info(f"    🇺🇸 US_INTERNAL: {kw}")
            return True

    return False


def is_relevant(article: Article) -> bool:
    """Главный фильтр релевантности. Логирует ВСЕ причины отказа."""
    text = f"{article.title} {article.summary}".lower()

    # 1. Проверка возраста
    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        logger.info(f"  ⏰ TOO_OLD ({age_hours:.0f}h): {article.title[:50]}")
        return False

    # 2. Должны быть AI ключевые слова
    ai_score = ai_relevance_score(text)
    if ai_score == 0:
        logger.info(f"  🚫 NO_AI_KW: {article.title[:60]}")
        return False

    # 3. Реклама — всегда блок
    for bad in BAD_PHRASES:
        if bad in text:
            logger.info(f"  🚫 AD ({bad}): {article.title[:50]}")
            return False

    # 4. HARD exclude — блокируется ВСЕГДА, даже если есть AI
    for ex in HARD_EXCLUDE_KEYWORDS:
        if ex in text:
            logger.info(f"  🚫 HARD_EXCLUDE ({ex}): {article.title[:50]}")
            return False

    # 5. SOFT exclude — блокируется ТОЛЬКО если AI-контекст слабый (0-1 слово)
    if ai_score <= 1:
        for ex in SOFT_EXCLUDE_KEYWORDS:
            if ex in text:
                logger.info(f"  🚫 SOFT_EXCLUDE ({ex}, ai_score={ai_score}): {article.title[:50]}")
                return False

    # 6. Чистая экономика
    if is_economics_news(text):
        logger.info(f"  💵 ECON: {article.title[:50]}")
        return False

    # 7. Локальные US новости
    if is_local_us_news(text):
        logger.info(f"  🇺🇸 LOCAL_US: {article.title[:50]}")
        return False

    logger.info(f"  ✅ PASS (ai={ai_score}): {article.title[:60]}")
    return True


# ====================== DUPLICATE RESULT ======================
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
        self._lock = threading.Lock()
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_file, timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA busy_timeout=30000')
        return self._conn

    def _init_db(self):
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

        indices = [
            ('idx_norm_url', 'norm_url'),
            ('idx_content_hash', 'content_hash'),
            ('idx_domain', 'domain'),
            ('idx_posted_date', 'posted_date'),
            ('idx_title_normalized', 'title_normalized'),
            ('idx_title_word_signature', 'title_word_signature'),
        ]
        for idx_name, column in indices:
            cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON posted_articles({column})')

        conn.commit()
        logger.info("📚 База данных инициализирована")

    def _was_rejected(self, norm_url: str) -> bool:
        cursor = self._get_conn().cursor()
        cursor.execute('SELECT 1 FROM rejected_urls WHERE norm_url = ?', (norm_url,))
        return cursor.fetchone() is not None

    def _add_rejected(self, norm_url: str, title: str, reason: str):
        try:
            cursor = self._get_conn().cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO rejected_urls (norm_url, title, reason) VALUES (?, ?, ?)',
                (norm_url, title[:200], reason)
            )
            self._get_conn().commit()
        except Exception as e:
            logger.error(f"Ошибка добавления в rejected: {e}")

    def is_duplicate(self, url: str, title: str, summary: str = "") -> DuplicateCheckResult:
        result = DuplicateCheckResult(is_duplicate=False, reasons=[])

        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            norm_url = normalize_url(url)
            title_normalized = normalize_title(title)
            title_words = set(get_title_words(title))
            word_signature = get_sorted_word_signature(title)
            content_hash = get_content_hash(f"{title} {summary}")
            entities = extract_entities(f"{title} {summary}")
            domain = get_domain(url)

            # Проверка rejected — только по URL, не по заголовку
            if self._was_rejected(norm_url):
                result.add_reason("PREVIOUSLY_REJECTED")
                return result

            # Точное совпадение URL
            cursor.execute('SELECT title FROM posted_articles WHERE norm_url = ?', (norm_url,))
            row = cursor.fetchone()
            if row:
                result.add_reason("URL_EXACT", 1.0, row[0])
                return result

            # Content hash
            if content_hash:
                cursor.execute('SELECT title FROM posted_articles WHERE content_hash = ?', (content_hash,))
                row = cursor.fetchone()
                if row:
                    result.add_reason("CONTENT_HASH", 1.0, row[0])
                    return result

            # Точное совпадение нормализованного заголовка
            cursor.execute('SELECT title FROM posted_articles WHERE title_normalized = ?', (title_normalized,))
            row = cursor.fetchone()
            if row:
                result.add_reason("TITLE_EXACT", 1.0, row[0])
                return result

            # Word signature
            cursor.execute('SELECT title FROM posted_articles WHERE title_word_signature = ?', (word_signature,))
            row = cursor.fetchone()
            if row:
                result.add_reason("WORD_SIGNATURE", 0.95, row[0])
                return result

            # Fuzzy сравнение с существующими постами
            cursor.execute(
                'SELECT id, title, title_normalized, title_words, entities, domain FROM posted_articles')
            all_posts = cursor.fetchall()

            for row in all_posts:
                existing_id, existing_title, existing_normalized, existing_words_json, existing_entities_json, existing_domain = row

                # Sequence similarity
                seq_sim = calculate_similarity(title_normalized, existing_normalized)
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)

                # N-gram similarity
                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)

                # Jaccard
                if existing_words_json:
                    try:
                        existing_words = set(json.loads(existing_words_json))
                        jaccard = jaccard_similarity(title_words, existing_words)
                        if jaccard > config.jaccard_threshold:
                            result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)
                    except Exception:
                        pass

                # Entity overlap
                if entities and existing_entities_json:
                    try:
                        existing_entities = set(json.loads(existing_entities_json))
                        if len(entities) >= 2 and len(existing_entities) >= 2:
                            common = entities & existing_entities
                            min_size = min(len(entities), len(existing_entities))
                            overlap = len(common) / min_size if min_size > 0 else 0

                            if len(common) >= 2 and overlap >= config.entity_overlap_threshold:
                                result.add_reason(f"ENTITY ({len(common)})", overlap, existing_title)
                    except Exception:
                        pass

                # Same domain — stricter
                if domain == existing_domain:
                    same_sim = calculate_similarity(title_normalized, existing_normalized)
                    if same_sim > config.same_domain_similarity:
                        result.add_reason(f"SAME_DOMAIN ({same_sim:.0%})", same_sim, existing_title)

            return result

    def check_diversity(self, topic: str) -> Tuple[bool, str]:
        with self._lock:
            if topic == Topic.GENERAL:
                return True, ""

            cursor = self._get_conn().cursor()
            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,)
            )
            recent_topics = [row[0] for row in cursor.fetchall()]

            if not recent_topics:
                return True, ""

            if recent_topics[0] == topic:
                return False, f"SAME_AS_LAST: {topic}"

            same_count = sum(1 for t in recent_topics if t == topic)
            if same_count >= config.same_topic_limit:
                return False, f"TOO_MANY: {same_count}/{config.diversity_window} = {topic}"

            return True, ""

    def add(self, article: Article, topic: str = Topic.GENERAL) -> bool:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            norm_url = normalize_url(article.link)
            domain_val = get_domain(article.link)
            title_normalized = normalize_title(article.title)
            title_words = list(get_title_words(article.title))
            word_signature = get_sorted_word_signature(article.title)
            content_hash = get_content_hash(f"{article.title} {article.summary}")
            entities = list(extract_entities(f"{article.title} {article.summary}"))

            try:
                cursor.execute('''
                    INSERT INTO posted_articles 
                    (url, norm_url, domain, title, title_normalized, title_words, 
                     title_word_signature, summary, content_hash, entities, topic, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.link, norm_url, domain_val, article.title, title_normalized,
                    json.dumps(title_words), word_signature, article.summary[:1000],
                    content_hash, json.dumps(entities), topic, article.source
                ))

                conn.commit()

                cursor.execute('SELECT id FROM posted_articles WHERE norm_url = ?', (norm_url,))
                saved = cursor.fetchone()

                if saved:
                    logger.info(f"💾 Сохранено (ID={saved[0]}): {article.title[:50]}...")
                    return True
                else:
                    logger.error(f"❌ Не сохранено: {article.title[:50]}")
                    return False

            except sqlite3.IntegrityError:
                logger.warning(f"⚠️ Уже существует: {article.title[:40]}")
                return False
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                return False

    def log_rejected(self, article: Article, reason: str):
        norm_url = normalize_url(article.link)
        self._add_rejected(norm_url, article.title, reason)
        logger.info(f"🚫 [{reason}]: {article.title[:50]}")

    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, topic, source, posted_date 
                FROM posted_articles 
                ORDER BY posted_date DESC 
                LIMIT ?
            ''', (limit,))

            return [{'title': r[0], 'topic': r[1], 'source': r[2], 'date': r[3]} for r in cursor.fetchall()]

    def cleanup(self, days: int = 90):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute(f"DELETE FROM posted_articles WHERE posted_date < datetime('now', '-{days} days')")
            deleted = cursor.rowcount

            # Rejected чистим чаще — через 7 дней, чтобы не копились
            cursor.execute(
                f"DELETE FROM rejected_urls WHERE rejected_at < datetime('now', '-{config.rejected_retention_days} days')")
            rejected_deleted = cursor.rowcount

            conn.commit()

            if deleted > 0 or rejected_deleted > 0:
                logger.info(f"🧹 Очищено: {deleted} posted, {rejected_deleted} rejected")

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
        if self._conn:
            try:
                self._conn.commit()
                self._conn.close()
                logger.info("🔒 БД закрыта")
            except Exception as e:
                logger.error(f"❌ Ошибка закрытия: {e}")
            finally:
                self._conn = None


# ====================== AUTO-CLEANUP ECONOMICS ======================
def auto_cleanup_economics(posted: PostedManager):
    logger.info("🧹 Проверка экономических постов...")

    econ_terms = [
        "inflation", "federal reserve", "fed rate", "recession", "fed ",
        "gdp", "unemployment", "stock market", "bonds", "treasury",
        "инфляция", "фрс", "бостик", "уорша", "процентная ставка",
        "центральный банк", "валюта", "экономический рост"
    ]

    with posted._lock:
        conn = posted._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT id, title, summary FROM posted_articles")
        all_posts = cursor.fetchall()

        deleted = 0
        for post_id, title, summary in all_posts:
            text = f"{title} {summary}".lower()
            econ_count = sum(1 for term in econ_terms if term in text)

            if econ_count >= 2:
                ai_kw = ["ai", "artificial intelligence", "machine learning",
                         "llm", "gpt", "claude", "gemini", "нейро", "ии"]
                has_ai = any(kw in text for kw in ai_kw)

                if not has_ai:
                    cursor.execute("DELETE FROM posted_articles WHERE id = ?", (post_id,))
                    deleted += 1
                    logger.info(f"  🗑️ ID={post_id}: {title[:50]}")

        conn.commit()

        if deleted > 0:
            logger.info(f"🗑️ Удалено {deleted} экономических постов")
        else:
            logger.info("✅ Экономических постов не найдено")


# ====================== RSS LOADING ======================
async def fetch_feed(url: str, source: str) -> List[Article]:
    try:
        await asyncio.sleep(random.uniform(0.5, 2))

        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning(f"  ⚠️ {source}: HTTP {resp.status}")
                    return []
                content = await resp.text()

        feed = await asyncio.to_thread(feedparser.parse, content)

        articles = []
        for entry in feed.entries[:15]:
            link = entry.get('link', '').strip()
            title = entry.get('title', '').strip()
            summary = re.sub(r'<[^>]+>', '',
                             entry.get('summary', entry.get('description', '')).strip())

            if not link or not title or len(title) < 20:
                continue

            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = datetime(*pub_date[:6], tzinfo=timezone.utc) if pub_date else datetime.now(timezone.utc)

            articles.append(Article(title=title, summary=summary, link=link,
                                    source=source, published=published))

        logger.info(f"  ✅ {source}: {len(articles)}")
        return articles

    except Exception as e:
        logger.warning(f"  ⚠️ {source}: {e}")
        return []


async def load_all_feeds() -> List[Article]:
    logger.info("📥 Загрузка RSS...")
    tasks = [fetch_feed(url, source) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks)

    all_articles = []
    for feed_articles in results:
        all_articles.extend(feed_articles)

    logger.info(f"📦 Всего: {len(all_articles)}")
    return all_articles


# ====================== FILTERING ======================
def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("🔍 Фильтрация...")
    logger.info(f"   Входящих статей: {len(articles)}")

    candidates = []
    seen_normalized_titles: Set[str] = set()
    seen_word_signatures: Set[str] = set()
    seen_content_hashes: Set[str] = set()

    # Счётчики для итоговой статистики
    stats = {
        "too_old": 0, "no_ai": 0, "ad": 0, "hard_exclude": 0,
        "soft_exclude": 0, "econ": 0, "us_local": 0,
        "batch_dup": 0, "db_dup": 0, "diversity": 0, "passed": 0
    }

    for article in articles:
        if not is_relevant(article):
            continue

        title_normalized = normalize_title(article.title)
        if title_normalized in seen_normalized_titles:
            logger.info(f"  🔄 BATCH_DUP_TITLE: {article.title[:50]}")
            stats["batch_dup"] += 1
            continue

        word_sig = get_sorted_word_signature(article.title)
        if word_sig in seen_word_signatures:
            logger.info(f"  🔄 BATCH_DUP_SIG: {article.title[:50]}")
            stats["batch_dup"] += 1
            continue

        content_hash = get_content_hash(f"{article.title} {article.summary}")
        if content_hash in seen_content_hashes:
            logger.info(f"  🔄 BATCH_DUP_HASH: {article.title[:50]}")
            stats["batch_dup"] += 1
            continue

        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            reason = "; ".join(dup_result.reasons[:3])
            posted.log_rejected(article, reason)
            stats["db_dup"] += 1
            continue

        topic = Topic.detect(f"{article.title} {article.summary}")
        div_ok, div_reason = posted.check_diversity(topic)
        if not div_ok:
            posted.log_rejected(article, div_reason)
            stats["diversity"] += 1
            continue

        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)

        candidates.append(article)
        stats["passed"] += 1

    def score(art: Article) -> float:
        entities = extract_entities(f"{art.title} {art.summary}")
        age = (datetime.now(timezone.utc) - art.published).total_seconds() / 3600
        ai_score = ai_relevance_score(f"{art.title} {art.summary}")
        return len(entities) * 2 + ai_score * 1.5 + max(0, 48 - age) / 48

    candidates.sort(key=score, reverse=True)

    logger.info(f"📊 Итоги фильтрации:")
    logger.info(f"   batch_dup={stats['batch_dup']}, db_dup={stats['db_dup']}, "
                f"diversity={stats['diversity']}, passed={stats['passed']}")
    logger.info(f"✅ Кандидатов: {len(candidates)} из {len(articles)}")

    return candidates


# ====================== TEXT GENERATION ======================
async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация: {article.title[:55]}...")

    prompt = f"""Ты — редактор Telegram-канала про технологии для аудитории из РФ и СНГ.

НОВОСТЬ:
{article.title}
{article.summary[:800]}

ЗАДАЧА: Адаптируй новость для российского читателя.

СТРУКТУРА:
1. 🔥 Заголовок (цепляющий, на русском, без "США")
2. Суть — что произошло (факты)
3. Взгляд из РФ — почему это важно нам? (Если новость про блокировки/санкции/глобальные тренды — подчеркни это. Если это чисто внутренняя политика США — напиши SKIP).
4. Вывод

ТРЕБОВАНИЯ:
✅ Если новость про суды в Техасе, забастовки в Нью-Йорке или локальные разборки с полицией США — ответь ТОЛЬКО: SKIP
✅ Убирай чисто американский контекст (названия местных законов, имена сенаторов), оставляй суть технологии.
✅ Длина: 700-900 символов.

ЗАПРЕЩЕНО:
❌ Упоминать узко-американские детали без объяснения
❌ Писать так, будто читатель живёт в США
❌ Фразы: "стоит отметить", "интересно, что", "важно понимать"
❌ Общие фразы без конкретики

ПОСТ:"""

    for model in GROQ_MODELS:
        for attempt in range(config.groq_retries_per_model):
            try:
                await asyncio.sleep(1)
                logger.info(f"  🤖 {model} (попытка {attempt + 1})")

                resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model,
                    temperature=0.7,
                    max_tokens=1200,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.choices[0].message.content.strip()

                if "SKIP" in text.upper()[:10]:
                    logger.info("  ⏭️ SKIP (локальная новость)")
                    return None

                if len(text) < config.min_post_length:
                    logger.warning(f"  ⚠️ Короткий ({len(text)}), следующая модель...")
                    break

                water_phrases = ["стоит отметить", "важно понимать", "интересно, что",
                                 "давайте разберёмся", "как мы знаем", "не секрет"]
                if any(w in text.lower() for w in water_phrases):
                    logger.warning("  ⚠️ Вода, повтор...")
                    continue

                topic = Topic.detect(f"{article.title} {article.summary}")
                hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])

                cta = "\n\n🔥 — огонь  |  🗿 — мимо  |  ⚡ — интересно"
                source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'

                final = f"{text}{cta}\n\n{hashtags}{source_link}"

                logger.info(f"  ✅ [{model}]: {len(text)} симв.")
                return final

            except Exception as e:
                error_str = str(e).lower()
                if any(x in error_str for x in ["decommissioned", "deprecated", "not found"]):
                    logger.warning(f"  ⚠️ {model} недоступна")
                    break
                logger.error(f"  ❌ {model}: {e}")
                await asyncio.sleep(config.groq_base_delay * (2 ** attempt))

    logger.error("  ❌ Все модели не сработали")
    return None


# ====================== POSTING ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    topic = Topic.detect(f"{article.title} {article.summary}")

    try:
        logger.info(f"  📤 Отправка поста...")
        await bot.send_message(
            config.channel_id,
            text,
            disable_web_page_preview=False
        )

        saved = posted.add(article, topic)
        if saved:
            logger.info(f"✅ ОПУБЛИКОВАНО [{topic}]: {article.title[:50]}")
        return True

    except Exception as e:
        logger.error(f"❌ Telegram: {e}")
        return False


# ====================== MAIN ======================
async def main():
    logger.info("=" * 60)
    logger.info("🚀 AI-POSTER v9.1 (Clean Logging)")
    logger.info("=" * 60)

    posted = PostedManager(config.db_file)

    try:
        if posted.verify_db():
            logger.info("✅ БД OK")
        else:
            logger.error("❌ Проблема с БД!")
            return

        posted.cleanup(config.retention_days)
        auto_cleanup_economics(posted)

        stats = posted.get_stats()
        logger.info(f"📊 Статистика: {stats['total_posted']} posted, {stats['total_rejected']} rejected")

        recent = posted.get_recent_posts(3)
        if recent:
            logger.info("📋 Последние посты:")
            for p in recent:
                logger.info(f"   • [{p['topic']}] {p['title'][:60]}...")

        # Загрузка
        raw = await load_all_feeds()

        # Краткая сводка по источникам
        sources_count = {}
        for art in raw:
            sources_count[art.source] = sources_count.get(art.source, 0) + 1
        logger.info(f"📰 Источники: {sources_count}")

        # Фильтрация
        candidates = filter_and_dedupe(raw, posted)

        if not candidates:
            logger.info("📭 Нет подходящих новостей")
            return

        logger.info(f"🎯 Топ кандидаты:")
        for i, c in enumerate(candidates[:5]):
            logger.info(f"  {i + 1}. [{c.source}] {c.title[:70]}")

        for article in candidates[:25]:
            dup_result = posted.is_duplicate(article.link, article.title, article.summary)
            if dup_result.is_duplicate:
                posted.log_rejected(article, f"FINAL: {'; '.join(dup_result.reasons[:2])}")
                continue

            summary = await generate_summary(article)
            if not summary:
                posted.log_rejected(article, "GENERATION_FAILED")
                continue

            if await post_article(article, summary, posted):
                logger.info("\n🏁 Готово!")
                break

            await asyncio.sleep(2)
        else:
            logger.info("😔 Не удалось опубликовать")

    finally:
        posted.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())












































































































































































































































































