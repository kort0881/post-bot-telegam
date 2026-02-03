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
from urllib.parse import urlparse, quote, parse_qs, urlencode
from dataclasses import dataclass, field

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
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
        self.caption_limit = 1024
        self.db_file = "posted_articles.db"
        
        self.title_similarity_threshold = 0.55
        self.ngram_similarity_threshold = 0.40
        self.entity_overlap_threshold = 0.45
        self.jaccard_threshold = 0.50
        self.same_domain_similarity = 0.40
        
        self.min_post_length = 700
        self.max_article_age_hours = 48
        
        self.diversity_window = 5
        self.same_topic_limit = 2
        
        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0

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

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

IMAGE_STYLES = {
    "llm": "modern AI brain visualization, neural network connections, glowing blue and purple gradient, digital consciousness, futuristic",
    "image_gen": "creative digital art studio, colorful palette, artistic AI creation, vibrant neon colors, abstract",
    "robotics": "sleek humanoid robot, high-tech laboratory, metallic chrome surfaces, dramatic lighting, futuristic factory",
    "hardware": "advanced computer chips, circuit boards closeup, neon green lights, technological precision, macro photography",
    "general": "abstract technology concept, digital innovation, modern geometric shapes, blue purple gradient, clean minimal"
}

RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED"),
    ("https://www.artificialintelligence-news.com/feed/", "AI News"),
    ("https://openai.com/blog/rss/", "OpenAI Blog"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://www.marktechpost.com/feed/", "MarkTechPost"),
]

AI_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model", "gpt", "chatgpt", "claude",
    "gemini", "grok", "llama", "mistral", "deepseek", "midjourney",
    "dall-e", "stable diffusion", "sora", "openai", "anthropic",
    "deepmind", "nvidia", "agi", "transformer", "generative"
]

EXCLUDE_KEYWORDS = [
    "stock price", "ipo", "earnings call", "quarterly results", "dividend",
    "market cap", "wall street", "ps5", "xbox", "nintendo", "game review",
    "netflix", "movie review", "box office", "bitcoin", "crypto", "blockchain",
    "nft", "ethereum", "election", "trump", "biden", "congress"
]

BAD_PHRASES = ["sponsored", "partner content", "advertisement", "black friday", "deal alert"]

KEY_ENTITIES = [
    "openai", "google", "meta", "microsoft", "anthropic", "nvidia", "apple",
    "amazon", "deepmind", "hugging face", "stability ai", "midjourney",
    "mistral", "cohere", "perplexity", "xai", "inflection",
    "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "claude 3", "claude 3.5",
    "gemini", "gemini 2", "llama", "llama 3", "mistral", "mixtral",
    "copilot", "dall-e", "sora", "stable diffusion", "flux", "grok",
    "deepseek", "qwen", "o1", "o3",
    "linux foundation", "agentic", "ai agent", "agi", "regulation", "safety",
    "alignment", "open source", "robotics", "humanoid",
    "transformer", "diffusion", "multimodal", "reasoning", "fine-tuning",
    "rlhf", "rag", "vector database", "embedding"
]


@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
        if any(x in t for x in ["dall-e", "midjourney", "stable diffusion", "sora", "image"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "chip", "gpu", "hardware"]):
            return Topic.HARDWARE
        return Topic.GENERAL


# ====================== ИСПРАВЛЕНИЕ #2: normalize_url без протокола ======================
def normalize_url(url: str) -> str:
    """
    Нормализация URL с удалением протокола (http/https)
    для предотвращения дубликатов из-за разницы протоколов
    """
    try:
        # Убираем протокол полностью
        u = url.lower().strip()
        u = u.replace("https://", "").replace("http://", "")
        
        # Убираем www.
        u = u.replace("www.", "")
        
        # Парсим для удаления tracking параметров
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
        
        # Убираем trailing slash
        u = u.rstrip("/")
        
        return u
    except:
        # Fallback: просто убираем протокол и www
        return url.lower().strip().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def get_domain(url: str) -> str:
    try:
        u = url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
        return u.split("/")[0]
    except:
        return ""


def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'(\w+)\s*[-.]?\s*(\d+(?:\.\d+)?)', lambda m: m.group(1) + m.group(2).replace('.', ''), t)
    return t


def get_title_words(title: str) -> Set[str]:
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
    return {w for w in words if len(w) > 2 and w not in stop_words}


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
        return set(' '.join(words[i:i+n]) for i in range(len(words) - n + 1))
    
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
            domain = get_domain(url)
            title_normalized = normalize_title(title)
            title_words = get_title_words(title)
            word_signature = get_sorted_word_signature(title)
            content_hash = get_content_hash(f"{title} {summary}")
            entities = extract_entities(f"{title} {summary}")
            
            if self._was_rejected(norm_url):
                result.add_reason("PREVIOUSLY_REJECTED")
                return result
            
            cursor.execute('SELECT title FROM posted_articles WHERE norm_url = ?', (norm_url,))
            row = cursor.fetchone()
            if row:
                result.add_reason(f"URL_EXACT", 1.0, row[0])
                return result
            
            if content_hash:
                cursor.execute('SELECT title FROM posted_articles WHERE content_hash = ?', (content_hash,))
                row = cursor.fetchone()
                if row:
                    result.add_reason(f"CONTENT_HASH", 1.0, row[0])
                    return result
            
            cursor.execute('SELECT title FROM posted_articles WHERE title_normalized = ?', (title_normalized,))
            row = cursor.fetchone()
            if row:
                result.add_reason(f"TITLE_EXACT", 1.0, row[0])
                return result
            
            cursor.execute('SELECT title FROM posted_articles WHERE title_word_signature = ?', (word_signature,))
            row = cursor.fetchone()
            if row:
                result.add_reason(f"WORD_SIGNATURE", 0.95, row[0])
                return result
            
            cursor.execute('SELECT id, title, title_normalized, title_words, entities, domain FROM posted_articles')
            all_posts = cursor.fetchall()
            
            for row in all_posts:
                existing_id, existing_title, existing_normalized, existing_words_json, existing_entities_json, existing_domain = row
                
                seq_sim = calculate_similarity(title_normalized, existing_normalized)
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)
                
                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)
                
                if existing_words_json:
                    try:
                        existing_words = set(json.loads(existing_words_json))
                        jaccard = jaccard_similarity(title_words, existing_words)
                        if jaccard > config.jaccard_threshold:
                            result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)
                    except:
                        pass
                
                if entities and existing_entities_json:
                    try:
                        existing_entities = set(json.loads(existing_entities_json))
                        if len(entities) >= 2 and len(existing_entities) >= 2:
                            common = entities & existing_entities
                            min_size = min(len(entities), len(existing_entities))
                            overlap = len(common) / min_size if min_size > 0 else 0
                            
                            if len(common) >= 2 and overlap >= config.entity_overlap_threshold:
                                result.add_reason(f"ENTITY ({len(common)})", overlap, existing_title)
                    except:
                        pass
                
                if domain == existing_domain:
                    same_sim = calculate_similarity(title_normalized, existing_normalized)
                    if same_sim > config.same_domain_similarity:
                        result.add_reason(f"SAME_DOMAIN ({same_sim:.0%})", same_sim, existing_title)
            
            return result

    # ====================== ИСПРАВЛЕНИЕ #3: check_diversity с исключением для general ======================
    def check_diversity(self, topic: str) -> Tuple[bool, str]:
        """
        Проверка разнообразия тем.
        Для темы 'general' разрешаем повторы (80% новостей — general).
        Для узких тем (robotics, hardware) — строгая проверка.
        """
        with self._lock:
            # Для general всегда разрешаем
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
            
            # Для узких тем: не постим если последний пост такой же
            if recent_topics[0] == topic:
                return False, f"SAME_AS_LAST: {topic}"
            
            # Не больше N постов одной узкой темы в окне
            same_count = sum(1 for t in recent_topics if t == topic)
            if same_count >= config.same_topic_limit:
                return False, f"TOO_MANY: {same_count}/{config.diversity_window} = {topic}"
            
            return True, ""

    def add(self, article: Article, topic: str = Topic.GENERAL) -> bool:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            norm_url = normalize_url(article.link)
            domain = get_domain(article.link)
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
                    article.link, norm_url, domain, article.title, title_normalized,
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
            
            cursor.execute("DELETE FROM rejected_urls WHERE rejected_at < datetime('now', '-30 days')")
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
            except:
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


async def fetch_feed(url: str, source: str) -> List[Article]:
    try:
        await asyncio.sleep(random.uniform(0.5, 2))
        
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return []
                content = await resp.text()
        
        feed = await asyncio.to_thread(feedparser.parse, content)
        
        articles = []
        for entry in feed.entries[:15]:
            link = entry.get('link', '').strip()
            title = entry.get('title', '').strip()
            summary = re.sub(r'<[^>]+>', '', entry.get('summary', entry.get('description', '')).strip())
            
            if not link or not title or len(title) < 20:
                continue
            
            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = datetime(*pub_date[:6], tzinfo=timezone.utc) if pub_date else datetime.now(timezone.utc)
            
            articles.append(Article(title=title, summary=summary, link=link, source=source, published=published))
        
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


def is_relevant(article: Article) -> bool:
    text = f"{article.title} {article.summary}".lower()
    
    if any(bad in text for bad in BAD_PHRASES):
        return False
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    if not any(kw in text for kw in AI_KEYWORDS):
        return False
    
    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        return False
    
    return True


def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("🔍 Фильтрация...")
    
    candidates = []
    seen_normalized_titles: Set[str] = set()
    seen_word_signatures: Set[str] = set()
    seen_content_hashes: Set[str] = set()
    
    for article in articles:
        if not is_relevant(article):
            continue
        
        title_normalized = normalize_title(article.title)
        if title_normalized in seen_normalized_titles:
            continue
        
        word_sig = get_sorted_word_signature(article.title)
        if word_sig in seen_word_signatures:
            continue
        
        content_hash = get_content_hash(f"{article.title} {article.summary}")
        if content_hash in seen_content_hashes:
            continue
        
        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            reason = "; ".join(dup_result.reasons[:3])
            posted.log_rejected(article, reason)
            continue
        
        topic = Topic.detect(f"{article.title} {article.summary}")
        div_ok, div_reason = posted.check_diversity(topic)
        if not div_ok:
            posted.log_rejected(article, div_reason)
            continue
        
        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)
        
        candidates.append(article)
    
    def score(art: Article) -> float:
        entities = extract_entities(f"{art.title} {art.summary}")
        age = (datetime.now(timezone.utc) - art.published).total_seconds() / 3600
        return len(entities) * 2 + max(0, 48 - age) / 48
    
    candidates.sort(key=score, reverse=True)
    logger.info(f"✅ Кандидатов: {len(candidates)}")
    return candidates


async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация: {article.title[:55]}...")
    
    prompt = f"""Превратите AI-новость в пост для Telegram-канала.

НОВОСТЬ:
{article.title}
{article.summary[:800]}

СТРУКТУРА ПОСТА:
1. 🔥 Заголовок (5-8 слов, цепляющий, с эмодзи)
2. Суть — что произошло (2-3 предложения с фактами и цифрами)
3. Почему важно (2-3 предложения, контекст и последствия)
4. Вывод или провокационный вопрос к читателям

ЖЁСТКИЕ ТРЕБОВАНИЯ:
✅ Длина: СТРОГО 700-950 символов (меньше 700 = переделка!)
✅ Только факты, цифры, имена, даты
✅ Русский язык
✅ Каждый абзац должен нести новую информацию

ЗАПРЕЩЕНО:
❌ "стоит отметить", "интересно, что", "важно понимать"
❌ "давайте разберёмся", "как мы знаем", "не секрет"
❌ Общие фразы и вода

Если новость — мусор или реклама, ответь: SKIP

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
                    logger.info("  ⏭️ SKIP")
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

                if len(final) > config.caption_limit:
                    text = text[:config.caption_limit - 150]
                    for p in ['. ', '! ', '? ']:
                        idx = text.rfind(p)
                        if idx > len(text) * 0.5:
                            text = text[:idx+1]
                            break
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


# ====================== ИСПРАВЛЕНИЕ #1: generate_image с рабочими API ======================
async def generate_image(title: str, topic: str = None) -> Optional[str]:
    """
    Генерация изображения с 3 fallback API:
    1. Pollinations.ai (AI-генерация)
    2. LoremFlickr (реальные фото, замена Unsplash)
    3. Picsum (абстрактные фото, всегда работает)
    """
    logger.info(f"  🎨 Генерация изображения...")
    
    if topic is None or topic not in IMAGE_STYLES:
        topic = "general"
    
    logger.info(f"  📋 Тема: {topic}")
    
    style = IMAGE_STYLES.get(topic, IMAGE_STYLES["general"])
    clean_title = re.sub(r'[^\w\s]', '', title)[:40].strip() or "technology"
    seed = random.randint(1, 99999)
    
    # API 1: Pollinations.ai (AI-генерация)
    async def try_pollinations() -> Optional[bytes]:
        prompt = f"{style}, {clean_title}, high quality, 4k"
        url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=1024&height=1024&nologo=true&seed={seed}"
        
        logger.info(f"  🔗 [Pollinations] Запрос...")
        
        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=60, connect=15)
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(url, timeout=timeout, headers=HEADERS) as resp:
                        logger.info(f"  📊 [Pollinations] HTTP {resp.status}")
                        
                        if resp.status == 429:
                            await asyncio.sleep(10)
                            continue
                        if resp.status != 200:
                            await asyncio.sleep(3)
                            continue
                        
                        data = await resp.read()
                        logger.info(f"  📦 [Pollinations] {len(data)} байт")
                        
                        if len(data) >= 5000:
                            return data
            except asyncio.TimeoutError:
                logger.warning(f"  ⏱️ [Pollinations] Таймаут")
            except Exception as e:
                logger.warning(f"  ❌ [Pollinations] {e}")
            await asyncio.sleep(3)
        return None
    
    # API 2: LoremFlickr (замена нерабочего Unsplash Source)
    async def try_loremflickr() -> Optional[bytes]:
        keywords = clean_title.replace(' ', ',')[:30]
        url = f"https://loremflickr.com/1024/1024/{quote(keywords)},technology,ai/all"
        
        logger.info(f"  🔗 [LoremFlickr] Запрос...")
        
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=timeout, allow_redirects=True) as resp:
                    logger.info(f"  📊 [LoremFlickr] HTTP {resp.status}")
                    
                    if resp.status == 200:
                        data = await resp.read()
                        logger.info(f"  📦 [LoremFlickr] {len(data)} байт")
                        if len(data) >= 5000:
                            return data
        except Exception as e:
            logger.warning(f"  ❌ [LoremFlickr] {e}")
        return None
    
    # API 3: Picsum (всегда работает)
    async def try_picsum() -> Optional[bytes]:
        url = f"https://picsum.photos/seed/{seed}/1024/1024"
        
        logger.info(f"  🔗 [Picsum] Запрос...")
        
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=timeout, allow_redirects=True) as resp:
                    logger.info(f"  📊 [Picsum] HTTP {resp.status}")
                    
                    if resp.status == 200:
                        data = await resp.read()
                        logger.info(f"  📦 [Picsum] {len(data)} байт")
                        if len(data) >= 5000:
                            return data
        except Exception as e:
            logger.warning(f"  ❌ [Picsum] {e}")
        return None
    
    # Пробуем API по очереди
    for api_name, api_func in [
        ("Pollinations", try_pollinations),
        ("LoremFlickr", try_loremflickr),
        ("Picsum", try_picsum)
    ]:
        data = await api_func()
        
        if data:
            is_jpeg = data[:3] == b'\xff\xd8\xff'
            is_png = data[:8] == b'\x89PNG\r\n\x1a\n'
            
            if is_jpeg or is_png:
                ext = "jpg" if is_jpeg else "png"
                fname = f"img_{random.randint(1000, 9999)}.{ext}"
                
                with open(fname, "wb") as f:
                    f.write(data)
                
                if os.path.exists(fname):
                    logger.info(f"  ✅ [{api_name}] Сохранено: {fname} ({len(data)//1024} KB)")
                    return fname
    
    logger.warning("  ❌ Все API не сработали")
    return None


async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    topic = Topic.detect(f"{article.title} {article.summary}")
    
    logger.info(f"  🎨 Начинаю генерацию изображения...")
    img = await generate_image(article.title, topic)
    
    try:
        if img and os.path.exists(img):
            logger.info(f"  📤 Отправка с картинкой: {img}")
            await bot.send_photo(config.channel_id, FSInputFile(img), caption=text)
            os.remove(img)
        else:
            logger.info(f"  📤 Отправка без картинки")
            await bot.send_message(config.channel_id, text, disable_web_page_preview=False)
        
        saved = posted.add(article, topic)
        if saved:
            logger.info(f"✅ ОПУБЛИКОВАНО [{topic}]: {article.title[:50]}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Telegram: {e}")
        if img and os.path.exists(img):
            try: os.remove(img)
            except: pass
        return False


async def main():
    logger.info("=" * 50)
    logger.info("🚀 AI-POSTER v6.1 (Final Fix)")
    logger.info("=" * 50)
    
    posted = PostedManager(config.db_file)
    
    try:
        if posted.verify_db():
            logger.info("✅ БД OK")
        else:
            logger.error("❌ Проблема с БД!")
            return
        
        posted.cleanup(config.retention_days)
        stats = posted.get_stats()
        logger.info(f"📊 Статистика: {stats['total_posted']} posted, {stats['total_rejected']} rejected")
        
        recent = posted.get_recent_posts(3)
        if recent:
            logger.info("📋 Последние:")
            for p in recent:
                logger.info(f"   • [{p['topic']}] {p['title'][:40]}...")
        
        raw = await load_all_feeds()
        candidates = filter_and_dedupe(raw, posted)
        
        if not candidates:
            logger.info("📭 Нет новостей")
            return

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

































































































































































































































































