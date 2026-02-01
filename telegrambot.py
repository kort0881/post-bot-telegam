import os
import json
import asyncio
import random
import re
import hashlib
import logging
import difflib
import tempfile
import shutil
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import List, Set, Optional, Tuple, Dict
from urllib.parse import urlparse, quote, parse_qs, urlencode
from dataclasses import dataclass, field
from collections import Counter
import math

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
        self.retention_days = int(os.getenv("RETENTION_DAYS", "60"))  # Увеличено до 60
        self.caption_limit = 1024
        self.db_file = "posted_articles.db"
        
        self.similarity_threshold = 0.72  # Увеличено с 0.60
        self.entity_overlap_threshold = 0.55
        self.min_post_length = 500
        self.min_summary_length = 200  # Увеличено с 100
        self.max_article_age_hours = 24  # Уменьшено с 72 (3 дня)
        
        # TF-IDF порог
        self.tfidf_similarity_threshold = 0.65
        
        # Параметры для разнообразия
        self.recent_posts_check = 5
        self.recent_similarity_threshold = 0.45
        self.min_entity_distance = 2
        self.diversity_window = 3  # Последние N постов для проверки разнообразия
        
        # Retry параметры
        self.groq_max_retries = 5
        self.groq_base_delay = 1.0
        self.groq_max_delay = 30.0
        
        # RSS параметры
        self.rss_timeout = 20
        self.rss_jitter = 3

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ====================== RSS (РАСШИРЕННЫЙ СПИСОК) ======================
RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED"),
    ("https://www.artificialintelligence-news.com/feed/", "AI News"),
    ("https://hai.stanford.edu/news/rss.xml", "Stanford HAI"),
    ("https://deepmind.google/blog/rss.xml", "DeepMind Blog"),
    ("https://openai.com/blog/rss/", "OpenAI Blog"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://www.marktechpost.com/feed/", "MarkTechPost"),
    ("https://syncedreview.com/feed/", "Synced AI"),
    ("https://news.ycombinator.com/rss", "Hacker News"),
    ("https://www.unite.ai/feed/", "Unite.AI"),
    ("https://analyticsindiamag.com/feed/", "AIM"),
]

# ====================== КЛЮЧЕВЫЕ СЛОВА ======================
AI_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model", "gpt", "chatgpt", "claude",
    "gemini", "grok", "llama", "mistral", "qwen", "deepseek", "midjourney",
    "dall-e", "stable diffusion", "sora", "groq", "openai", "anthropic",
    "deepmind", "hugging face", "nvidia", "agi", "transformer", "generative",
    "agents", "reasoning", "multimodal", "fine-tuning", "rlhf", "o3", "o1",
    "cursor", "copilot", "replit", "v0", "perplexity", "cohere", "01.ai"
]

EXCLUDE_KEYWORDS = [
    "stock price", "ipo", "earnings call", "quarterly results", "dividend",
    "market cap", "wall street", "ps5", "xbox", "nintendo", "game review",
    "netflix", "movie review", "box office", "trailer", "tesla stock",
    "bitcoin", "crypto", "blockchain", "nft", "ethereum", "election",
    "trump", "biden", "congress", "senate"
]

BAD_PHRASES = ["sponsored", "partner content", "advertisement", "black friday", "deal alert"]

# ====================== РАСШИРЕННЫЕ СУЩНОСТИ ======================
KEY_ENTITIES = [
    # Компании
    "openai", "google", "meta", "microsoft", "anthropic", "nvidia", "apple",
    "amazon", "deepmind", "hugging face", "stability ai", "midjourney",
    "mistral", "cohere", "perplexity", "runway", "pika", "character ai",
    "inflection", "xai", "baidu", "alibaba", "tencent", "bytedance",
    "01.ai", "moonshot ai", "zhipu ai", "ai21 labs", "adept", "adept ai",
    "elevenlabs", "heygen", "synthesia", "jasper", "copy.ai", "replika",
    
    # Модели и продукты
    "gpt-4", "gpt-5", "gpt-4o", "gpt-4.5", "chatgpt", "claude", "claude 3",
    "claude 3.5", "claude 3.5 sonnet", "claude 3 opus", "gemini", "gemini 2",
    "gemini 2.0", "gemini 1.5", "llama", "llama 3", "llama 3.3", "llama 3.2",
    "llama 3.1", "mistral", "mixtral", "mixtral 8x7b", "mixtral 8x22b",
    "copilot", "github copilot", "microsoft copilot", "dall-e", "dall-e 3",
    "dall-e 2", "sora", "stable diffusion", "stable diffusion 3", "flux",
    "midjourney v6", "midjourney v7", "runway gen", "runway gen-3", "firefly",
    "adobe firefly", "imagen", "imagen 3", "grok", "grok 2", "grok 3",
    "deepseek", "deepseek-v3", "deepseek-v2", "deepseek-r1", "qwen",
    "qwen 2", "qwen 2.5", "yi", "yi-34b", "command r", "command r+",
    "o3", "o3 mini", "o1", "o1 mini", "o1 preview", "gpt-4o mini",
    
    # Инструменты и платформы
    "cursor", "cursor ai", "replit", "replit agent", "v0", "v0.dev",
    "vercel v0", "bolt", "bolt.new", "lovable", "temporal", "langchain",
    "llamaindex", "crewai", "autogen", "semantic kernel", "haystack",
    "weaviate", "pinecone", "chroma", "qdrant", "milvus", "modal",
    "replicate", "together ai", "fireworks ai", "baseten", "banana dev",
    
    # Ключевые темы
    "linux foundation", "agentic", "ai agent", "ai agents", "agi", "asi",
    "artificial general intelligence", "regulation", "safety", "alignment",
    "ai safety", "ai alignment", "open source", "open-source", "open weights",
    "robotics", "humanoid", "boston dynamics", "figure", "figure ai",
    "optimus", "tesla bot", "unitree", "agility robotics", "digit",
    "apptronik", "1x technologies", "sanctuary ai", "covariant",
    
    # Технологии
    "transformer", "transformers", "diffusion", "diffusion model",
    "multimodal", "multimodal ai", "reasoning", "chain of thought",
    "cot", "fine-tuning", "rlhf", "inference", "training", "benchmark",
    "rag", "retrieval augmented generation", "vector database", "embedding",
    "token", "context window", "prompt engineering", "jailbreak",
    "hallucination", "ai hallucination", "synthetic data",
]

# ====================== DATACLASS ======================
@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

# ====================== TOPIC & HASHTAGS ======================
class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    REGULATION = "regulation"
    RESEARCH = "research"
    AGENTS = "agents"
    CODING = "coding"
    GENERAL = "general"
    
    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #StableDiffusion #ImageGen #ИИАрт",
        ROBOTICS: "#ИИ #роботы #робототехника #автоматизация",
        HARDWARE: "#NVIDIA #чипы #GPU #железо",
        REGULATION: "#регулирование #законы #этика #безопасность",
        RESEARCH: "#исследования #наука #ML #DeepLearning",
        AGENTS: "#агенты #автономность #AutoGPT #AI",
        CODING: "#программирование #код #разработка #DevTools",
        GENERAL: "#ИИ #технологии #инновации #AI"
    }
    
    IMAGE_STYLES = {
        LLM: "futuristic digital brain, circuit patterns, neural network visualization, blue purple gradient",
        IMAGE_GEN: "creative art studio, digital canvas, vibrant colors, artistic AI generation",
        ROBOTICS: "sleek humanoid robot, high-tech laboratory, metallic surfaces, dramatic lighting",
        HARDWARE: "advanced computer chips, circuit boards, neon lights, technological precision",
        REGULATION: "digital scales of justice, government building, legal documents, professional",
        RESEARCH: "scientific laboratory, data visualization, graphs and charts, academic",
        AGENTS: "autonomous systems, interconnected nodes, workflow automation, modern tech",
        CODING: "code editor interface, programming environment, dark theme, developer workspace",
        GENERAL: "abstract technology, digital innovation, modern tech aesthetic, clean design"
    }
    
    @staticmethod
    def detect(text: str) -> str:
        text_lower = text.lower()
        
        llm_terms = ["gpt", "claude", "gemini", "llm", "chatbot", "language model", 
                     "chatgpt", "llama", "mistral", "deepseek", "qwen", "reasoning"]
        if any(term in text_lower for term in llm_terms):
            return Topic.LLM
        
        image_terms = ["dall-e", "midjourney", "stable diffusion", "image generation",
                      "text-to-image", "imagen", "firefly", "flux", "sora", "video generation"]
        if any(term in text_lower for term in image_terms):
            return Topic.IMAGE_GEN
        
        robot_terms = ["robot", "humanoid", "automation", "robotic", "boston dynamics",
                      "figure ai", "optimus", "tesla bot"]
        if any(term in text_lower for term in robot_terms):
            return Topic.ROBOTICS
        
        hw_terms = ["nvidia", "chip", "gpu", "hardware", "semiconductor", "processor",
                   "tpu", "asic", "groq chip"]
        if any(term in text_lower for term in hw_terms):
            return Topic.HARDWARE
        
        reg_terms = ["regulation", "policy", "law", "government", "ethical", "ban",
                    "restriction", "compliance", "legal"]
        if any(term in text_lower for term in reg_terms):
            return Topic.REGULATION
        
        research_terms = ["research", "paper", "study", "breakthrough", "discovery",
                         "scientific", "experiment", "arxiv"]
        if any(term in text_lower for term in research_terms):
            return Topic.RESEARCH
        
        agent_terms = ["agent", "autonomous", "autogpt", "workflow", "automation tool",
                      "ai assistant", "personal ai"]
        if any(term in text_lower for term in agent_terms):
            return Topic.AGENTS
        
        code_terms = ["coding", "copilot", "cursor", "programming", "developer",
                     "ide", "code generation", "replit", "v0"]
        if any(term in text_lower for term in code_terms):
            return Topic.CODING
        
        return Topic.GENERAL
    
    @staticmethod
    def get_image_style(topic: str) -> str:
        return Topic.IMAGE_STYLES.get(topic, Topic.IMAGE_STYLES[Topic.GENERAL])

# ====================== GROQ МОДЕЛИ ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
]

# ====================== UTILITY FUNCTIONS ======================
def normalize_url(url: str) -> str:
    """Нормализация URL для поиска дубликатов"""
    parsed = urlparse(url.lower())
    
    # Убираем query параметры для трекинга
    if parsed.query:
        query_params = parse_qs(parsed.query)
        tracking_params = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 
                          'fbclid', 'gclid', 'ref', 'source'}
        clean_params = {k: v for k, v in query_params.items() if k not in tracking_params}
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ''
    else:
        clean_query = ''
    
    # Убираем trailing slash
    path = parsed.path.rstrip('/')
    
    # Формируем нормализованный URL
    norm = f"{parsed.netloc}{path}"
    if clean_query:
        norm += f"?{clean_query}"
    
    return norm

def get_domain(url: str) -> str:
    """Извлекает домен из URL"""
    parsed = urlparse(url.lower())
    domain = parsed.netloc
    # Убираем www.
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain

def get_title_signature(title: str) -> str:
    """Создаёт сигнатуру заголовка (первые 4 значимых слова)"""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', title.lower())
    return ' '.join(words[:4]) if words else title.lower()[:30]

def get_summary_hash(summary: str) -> str:
    """Хеш summary для быстрой проверки"""
    clean = re.sub(r'\s+', ' ', summary.lower().strip())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def get_content_hash(text: str, length: int = 200) -> Optional[str]:
    """Хеш первых N символов контента"""
    if not text or len(text) < 50:
        return None
    clean = re.sub(r'\s+', ' ', text.lower().strip())[:length]
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def calculate_similarity(str1: str, str2: str) -> float:
    """SequenceMatcher similarity"""
    return difflib.SequenceMatcher(None, str1.lower(), str2.lower()).ratio()

def ngram_similarity(str1: str, str2: str, n: int = 3) -> float:
    """N-gram similarity для обнаружения перефразированных заголовков"""
    def get_ngrams(text: str, n: int) -> Set[str]:
        words = text.lower().split()
        return set(' '.join(words[i:i+n]) for i in range(len(words) - n + 1))
    
    if len(str1.split()) < n or len(str2.split()) < n:
        return 0.0
    
    ngrams1 = get_ngrams(str1, n)
    ngrams2 = get_ngrams(str2, n)
    
    if not ngrams1 or not ngrams2:
        return 0.0
    
    intersection = len(ngrams1 & ngrams2)
    union = len(ngrams1 | ngrams2)
    
    return intersection / union if union > 0 else 0.0

def extract_key_entities(text: str) -> Set[str]:
    """Извлекает ключевые сущности из текста"""
    text_lower = text.lower()
    found = set()
    
    for entity in KEY_ENTITIES:
        # Точное совпадение или как отдельное слово
        if entity in text_lower:
            # Проверяем что это не часть другого слова
            pattern = r'\b' + re.escape(entity) + r'\b'
            if re.search(pattern, text_lower):
                found.add(entity)
    
    return found

def fuzzy_entity_match(entities1: Set[str], entities2: Set[str], threshold: float = 0.85) -> float:
    """Fuzzy matching сущностей для обнаружения вариаций (GPT-4 vs GPT4)"""
    if not entities1 or not entities2:
        return 0.0
    
    matches = 0
    for e1 in entities1:
        for e2 in entities2:
            sim = calculate_similarity(e1, e2)
            if sim >= threshold:
                matches += 1
                break
    
    max_size = max(len(entities1), len(entities2))
    return matches / max_size if max_size > 0 else 0.0

def tfidf_cosine_similarity(docs: List[str]) -> List[List[float]]:
    """TF-IDF косинусная близость между документами"""
    if len(docs) < 2:
        return [[1.0]]
    
    # Tokenize
    all_words = set()
    tokenized_docs = []
    for doc in docs:
        words = re.findall(r'\b\w+\b', doc.lower())
        tokenized_docs.append(words)
        all_words.update(words)
    
    # Term frequency
    tf_docs = []
    for words in tokenized_docs:
        word_count = Counter(words)
        total = len(words)
        tf = {word: count / total for word, count in word_count.items()}
        tf_docs.append(tf)
    
    # Inverse document frequency
    idf = {}
    num_docs = len(docs)
    for word in all_words:
        doc_count = sum(1 for tf in tf_docs if word in tf)
        idf[word] = math.log(num_docs / doc_count) if doc_count > 0 else 0
    
    # TF-IDF vectors
    tfidf_vectors = []
    for tf in tf_docs:
        vector = {word: tf.get(word, 0) * idf.get(word, 0) for word in all_words}
        tfidf_vectors.append(vector)
    
    # Cosine similarity
    similarity_matrix = []
    for i, vec1 in enumerate(tfidf_vectors):
        row = []
        for j, vec2 in enumerate(tfidf_vectors):
            if i == j:
                row.append(1.0)
            else:
                dot_product = sum(vec1.get(word, 0) * vec2.get(word, 0) for word in all_words)
                mag1 = math.sqrt(sum(v**2 for v in vec1.values()))
                mag2 = math.sqrt(sum(v**2 for v in vec2.values()))
                cos_sim = dot_product / (mag1 * mag2) if mag1 > 0 and mag2 > 0 else 0.0
                row.append(cos_sim)
        similarity_matrix.append(row)
    
    return similarity_matrix

import threading

# ====================== POSTED MANAGER (SQLite) ======================
class PostedManager:
    """SQLite-based менеджер с атомарными операциями и advisory locks"""
    
    def __init__(self, db_file: str = "posted_articles.db"):
        self.db_file = db_file
        self._lock = threading.Lock()
        self._conn = None
        self._init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Получает единое соединение с базой данных"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_file, timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Настройки для предотвращения блокировок
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA busy_timeout=30000')
        return self._conn
    
    def _init_db(self):
        """Инициализация базы данных"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Основная таблица posted_articles
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                norm_url TEXT NOT NULL,
                domain TEXT NOT NULL,
                title TEXT NOT NULL,
                title_signature TEXT NOT NULL,
                summary TEXT,
                content_hash TEXT,
                summary_hash TEXT NOT NULL,
                entities TEXT,  -- JSON список
                topic TEXT DEFAULT 'general',
                source TEXT,
                published_date TEXT,
                posted_date TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для отклонённых статей (логирование)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rejected_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                norm_url TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                source TEXT,
                rejection_reason TEXT NOT NULL,
                duplicate_of TEXT,  -- URL дубликата если есть
                similarity_score REAL,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Индексы для быстрого поиска
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_norm_url ON posted_articles(norm_url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_content_hash ON posted_articles(content_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_summary_hash ON posted_articles(summary_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_domain ON posted_articles(domain)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_title_signature ON posted_articles(title_signature)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_posted_date ON posted_articles(posted_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_topic ON posted_articles(topic)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rejected_url ON rejected_articles(url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rejected_reason ON rejected_articles(rejection_reason)')
        
        conn.commit()
        logger.info("📚 База данных инициализирована")
    
    def is_duplicate(self, url: str, title: str, summary: str = "") -> Tuple[bool, str]:
        """
        Многоуровневая проверка на дубликат:
        1. URL (нормализованный)
        2. Хеш summary
        3. Хеш контента
        4. Похожесть заголовка (n-gram + SequenceMatcher)
        5. Домен + сигнатура заголовка (ловит mirror-сайты)
        6. Пересечение сущностей
        
        Returns: (is_duplicate, reason)
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            norm_url = normalize_url(url)
            domain = get_domain(url)
            title_sig = get_title_signature(title)
            summary_hash = get_summary_hash(summary)
            content_hash = get_content_hash(summary)
            
            # 1. Проверка по нормализованному URL
            cursor.execute('SELECT title FROM posted_articles WHERE norm_url = ?', (norm_url,))
            if cursor.fetchone():
                return True, f"URL_DUPLICATE: {norm_url[:60]}"
            
            # 2. Проверка по хешу summary
            cursor.execute('SELECT title FROM posted_articles WHERE summary_hash = ?', (summary_hash,))
            if cursor.fetchone():
                return True, f"SUMMARY_HASH_DUPLICATE"
            
            # 3. Проверка по хешу контента
            if content_hash:
                cursor.execute('SELECT title FROM posted_articles WHERE content_hash = ?', (content_hash,))
                if cursor.fetchone():
                    return True, f"CONTENT_HASH_DUPLICATE"
            
            # 4. Проверка по домену + сигнатуре заголовка (mirror-сайты)
            cursor.execute('SELECT title FROM posted_articles WHERE domain = ? AND title_signature = ?',
                          (domain, title_sig))
            if cursor.fetchone():
                return True, f"DOMAIN_TITLE_SIGNATURE: {domain}"
            
            # 5. Проверка похожести заголовков
            cursor.execute('SELECT title FROM posted_articles WHERE posted_date > datetime("now", "-7 days")')
            recent_titles = [row[0] for row in cursor.fetchall()]
            
            for existing_title in recent_titles:
                # SequenceMatcher
                sim = calculate_similarity(title, existing_title)
                if sim > config.similarity_threshold:
                    return True, f"TITLE_SIMILARITY: {sim:.2f}"
                
                # N-gram similarity
                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.similarity_threshold:
                    return True, f"TITLE_NGRAM_SIMILARITY: {ngram_sim:.2f}"
            
            # 6. Проверка пересечения сущностей
            full_text = f"{title} {summary}".strip()
            new_entities = extract_key_entities(full_text)
            
            if len(new_entities) >= 2:
                cursor.execute('SELECT title, entities FROM posted_articles WHERE posted_date > datetime("now", "-14 days")')
                for row in cursor.fetchall():
                    existing_title, saved_entities_json = row
                    if saved_entities_json:
                        existing_entities = set(json.loads(saved_entities_json))
                    else:
                        existing_entities = extract_key_entities(existing_title)
                    
                    if len(existing_entities) < 2:
                        continue
                    
                    # Fuzzy matching сущностей
                    entity_sim = fuzzy_entity_match(new_entities, existing_entities)
                    if entity_sim >= config.entity_overlap_threshold:
                        return True, f"ENTITY_OVERLAP: {entity_sim:.2f}"
                    
                    # Прямое пересечение
                    common = new_entities & existing_entities
                    min_size = min(len(new_entities), len(existing_entities))
                    overlap_ratio = len(common) / min_size if min_size > 0 else 0
                    
                    if len(common) >= 2 and overlap_ratio >= config.entity_overlap_threshold:
                        return True, f"ENTITY_COMMON: {len(common)} entities"
            
            return False, ""
    
    def is_too_similar_to_recent(self, title: str, summary: str) -> Tuple[bool, str]:
        """
        Проверяет, не слишком ли похожа статья на последние N постов
        Использует TF-IDF cosine similarity
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute('SELECT title, summary, topic, entities FROM posted_articles '
                          'ORDER BY posted_date DESC LIMIT ?', (config.recent_posts_check,))
            recent_posts = cursor.fetchall()
            
            if len(recent_posts) < 2:
                return False, ""
            
            full_text = f"{title} {summary}".strip()
            new_entities = extract_key_entities(full_text)
            detected_topic = Topic.detect(full_text)
            
            # Собираем контекст для TF-IDF
            docs = [full_text]
            for row in recent_posts:
                docs.append(f"{row[0]} {row[1]}")
            
            # Вычисляем TF-IDF similarity
            similarity_matrix = tfidf_cosine_similarity(docs)
            
            # Проверяем похожесть с каждым недавним постом
            for idx, row in enumerate(recent_posts, start=1):
                sim_score = similarity_matrix[0][idx]
                
                if sim_score > config.tfidf_similarity_threshold:
                    return True, f"TFIDF_SIMILARITY: {sim_score:.2f} with '{row[0][:40]}...'"
                
                # Дополнительная проверка по сущностям
                if row[3]:  # entities
                    existing_entities = set(json.loads(row[3]))
                    common = new_entities & existing_entities
                    
                    if len(common) >= 3:
                        return True, f"TOO_MANY_COMMON_ENTITIES: {len(common)}"
            
            return False, ""
    
    def check_diversity_requirement(self, topic: str) -> Tuple[bool, str]:
        """
        Проверяет требования по разнообразию контента
        Не публикуем подряд посты из одной темы
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,)
            )
            recent_topics = [row[0] for row in cursor.fetchall()]
            
            if not recent_topics:
                return True, ""
            
            # Если последний пост такой же темы — отклоняем
            if recent_topics[0] == topic:
                return False, f"DIVERSITY: последний пост был {topic}"
            
            # Если 2 из 3 последних — такая же тема, отклоняем
            if len(recent_topics) >= config.diversity_window:
                same_topic_count = sum(1 for t in recent_topics[:config.diversity_window] if t == topic)
                if same_topic_count >= 2:
                    return False, f"DIVERSITY: {same_topic_count}/{config.diversity_window} последних — {topic}"
            
            return True, ""
    
    def llm_duplicate_check(self, article: Article, recent_posts: List[dict]) -> Tuple[bool, str]:
        """
        LLM-проверка: действительно ли это дубликат/похожая новость
        """
        if not recent_posts:
            return False, ""
        
        # Формируем контекст для LLM
        context = "НЕДАВНИЕ ПОСТЫ:\n"
        for i, post in enumerate(recent_posts[:3], 1):
            context += f"{i}. {post['title']}\n"
        
        new_article_text = f"НОВАЯ СТАТЬЯ:\n{article.title}\n{article.summary[:300]}"
        
        prompt = f"""{context}

{new_article_text}

Является ли новая статья дубликатом или очень похожей на недавние посты?
Отвечай ТОЛЬКО: YES или NO"""
        
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODELS[0],
                temperature=0.3,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.choices[0].message.content.strip().upper()
            
            if "YES" in answer:
                return True, "LLM_DUPLICATE_DETECTION"
        except Exception as e:
            logger.warning(f"⚠️ LLM duplicate check failed: {e}")
        
        return False, ""
    
    def add(self, article: Article, topic: str = Topic.GENERAL):
        """Добавляет статью в базу"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            norm_url = normalize_url(article.link)
            domain = get_domain(article.link)
            title_sig = get_title_signature(article.title)
            summary_hash = get_summary_hash(article.summary)
            content_hash = get_content_hash(article.summary)
            
            full_text = f"{article.title} {article.summary}".strip()
            entities = list(extract_key_entities(full_text))
            
            cursor.execute('''
                INSERT OR IGNORE INTO posted_articles 
                (url, norm_url, domain, title, title_signature, summary, 
                 content_hash, summary_hash, entities, topic, source, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article.link, norm_url, domain, article.title, title_sig, article.summary,
                content_hash, summary_hash, json.dumps(entities), topic, article.source,
                article.published.isoformat()
            ))
            
            conn.commit()
    
    def log_rejected(self, article: Article, reason: str, duplicate_of: str = None, similarity: float = None):
        """Логирует отклонённую статью"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            norm_url = normalize_url(article.link)
            
            cursor.execute('''
                INSERT INTO rejected_articles 
                (url, norm_url, title, summary, source, rejection_reason, duplicate_of, similarity_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article.link, norm_url, article.title, article.summary[:500],
                article.source, reason, duplicate_of, similarity
            ))
            
            conn.commit()
    
    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        """Возвращает последние N постов с метаданными"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT title, summary, topic, entities, url
                FROM posted_articles
                ORDER BY posted_date DESC
                LIMIT ?
            ''', (limit,))
            
            posts = []
            for row in cursor.fetchall():
                posts.append({
                    'title': row[0],
                    'summary': row[1],
                    'topic': row[2],
                    'entities': json.loads(row[3]) if row[3] else [],
                    'url': row[4]
                })
            return posts
    
    def get_recent_topics_stats(self) -> dict:
        """Возвращает статистику по темам последних постов"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT topic, COUNT(*) as count 
                FROM posted_articles 
                WHERE posted_date > datetime("now", "-7 days")
                GROUP BY topic
            ''')
            
            return {row[0]: row[1] for row in cursor.fetchall()}
    
    def cleanup(self, days: int = 60):
        """Удаляет записи старше N дней"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute(f'''
                DELETE FROM posted_articles 
                WHERE posted_date < datetime('now', '-{days} days')
            ''')
            
            deleted = cursor.rowcount
            conn.commit()
            
            # Очищаем старые rejected тоже
            cursor.execute('''
                DELETE FROM rejected_articles 
                WHERE checked_at < datetime("now", "-30 days")
            ''')
            rejected_deleted = cursor.rowcount
            conn.commit()
            
            if deleted > 0 or rejected_deleted > 0:
                logger.info(f"🧹 Очистка: удалено {deleted} posted, {rejected_deleted} rejected")
            
            # VACUUM для оптимизации
            if deleted > 100:
                cursor.execute('VACUUM')
                conn.commit()
    
    def get_stats(self) -> dict:
        """Возвращает статистику базы данных"""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM posted_articles')
            total_posted = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM rejected_articles')
            total_rejected = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT rejection_reason, COUNT(*) as count 
                FROM rejected_articles 
                GROUP BY rejection_reason
                ORDER BY count DESC
            ''')
            rejection_reasons = {row[0]: row[1] for row in cursor.fetchall()}
            
            return {
                'total_posted': total_posted,
                'total_rejected': total_rejected,
                'rejection_reasons': rejection_reasons
            }
    
    def close(self):
        """Закрывает соединение с базой данных"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("🔒 База данных закрыта")

# ====================== RSS LOADING ======================
async def fetch_feed(url: str, source: str, posted: PostedManager) -> List[Article]:
    """Загружает один RSS feed"""
    logger.info(f"📥 Загрузка: {source}")
    
    try:
        jitter = random.uniform(0, config.rss_jitter)
        await asyncio.sleep(jitter)
        
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=config.rss_timeout)) as resp:
                if resp.status != 200:
                    logger.warning(f"  ⚠️ HTTP {resp.status}")
                    return []
                
                content = await resp.text()
        
        feed = await asyncio.to_thread(feedparser.parse, content)
        
        if not feed.entries:
            logger.warning(f"  ⚠️ Нет записей")
            return []
        
        articles = []
        for entry in feed.entries[:15]:
            link = entry.get('link', '')
            title = entry.get('title', '').strip()
            summary = entry.get('summary', entry.get('description', '')).strip()
            
            if not link or not title:
                continue
            
            # Удаляем HTML теги из summary
            summary = re.sub(r'<[^>]+>', '', summary)
            
            # Парсим дату публикации
            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            if pub_date:
                published = datetime(*pub_date[:6], tzinfo=timezone.utc)
            else:
                published = datetime.now(timezone.utc)
            
            articles.append(Article(
                title=title,
                summary=summary,
                link=link,
                source=source,
                published=published
            ))
        
        logger.info(f"  ✅ {len(articles)} статей")
        return articles
        
    except asyncio.TimeoutError:
        logger.warning(f"  ⏱️ Таймаут")
        return []
    except Exception as e:
        logger.warning(f"  ❌ Ошибка: {e}")
        return []

async def load_all_feeds(posted: PostedManager) -> List[Article]:
    """Загружает все RSS feeds параллельно"""
    tasks = [fetch_feed(url, source, posted) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks)
    
    all_articles = []
    for feed_articles in results:
        all_articles.extend(feed_articles)
    
    logger.info(f"📦 Всего загружено: {len(all_articles)} статей")
    return all_articles

# ====================== ФИЛЬТРАЦИЯ ======================
def is_relevant(article: Article) -> bool:
    """Проверка релевантности статьи"""
    text_lower = f"{article.title} {article.summary}".lower()
    
    # Исключаем по плохим фразам
    if any(bad in text_lower for bad in BAD_PHRASES):
        return False
    
    # Исключаем по ключевым словам
    if any(ex in text_lower for ex in EXCLUDE_KEYWORDS):
        return False
    
    # Проверяем наличие AI ключевых слов
    has_ai_keyword = any(kw in text_lower for kw in AI_KEYWORDS)
    if not has_ai_keyword:
        return False
    
    # Проверка возраста статьи
    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        return False
    
    return True

def filter_articles(articles: List[Article], posted: PostedManager) -> List[Article]:
    """Фильтрует и сортирует статьи"""
    logger.info("🔍 Фильтрация статей...")
    
    candidates = []
    for article in articles:
        # Проверка релевантности
        if not is_relevant(article):
            continue
        
        # Проверка на дубликаты
        is_dup, reason = posted.is_duplicate(article.link, article.title, article.summary)
        if is_dup:
            posted.log_rejected(article, reason)
            continue
        
        # Проверка на похожесть с недавними
        is_similar, reason = posted.is_too_similar_to_recent(article.title, article.summary)
        if is_similar:
            posted.log_rejected(article, reason)
            continue
        
        candidates.append(article)
    
    # Сортировка по свежести и наличию сущностей
    def score_article(art: Article) -> float:
        text = f"{art.title} {art.summary}".lower()
        entities = extract_key_entities(text)
        entity_score = len(entities) * 0.5
        
        # Бонус за свежесть
        age_hours = (datetime.now(timezone.utc) - art.published).total_seconds() / 3600
        freshness_score = max(0, 24 - age_hours) / 24
        
        return entity_score + freshness_score
    
    candidates.sort(key=score_article, reverse=True)
    
    logger.info(f"✅ Отфильтровано: {len(candidates)} кандидатов")
    return candidates

# ====================== EXPONENTIAL BACKOFF ======================
def exponential_backoff(attempt: int) -> float:
    """Экспоненциальная задержка с jitter"""
    delay = min(config.groq_base_delay * (2 ** attempt), config.groq_max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter

# ====================== ГЕНЕРАЦИЯ САММАРИ ======================
async def generate_summary(article: Article) -> Optional[str]:
    """Генерирует пост через Groq"""
    logger.info(f"📝 Обработка: {article.title[:60]}...")
    
    prompt = f"""Превратите эту AI-новость в вирусный пост для Telegram-канала про нейросети.

НОВОСТЬ:
{article.title}
{article.summary[:800]}

СТРУКТУРА ПОСТА:
1. Взрывной заголовок (5-8 слов) — никакой воды, сразу к делу
2. Главная суть одним предложением (что случилось?)
3. Почему это важно (2-3 предложения без клише)
4. Краткий контекст или прогноз (1-2 предложения)

ВАЖНО:
× Никаких банальностей вроде "интересно отметить", "стоит сказать", "примечательно, что"
× Без очевидных вещей типа "ИИ развивается", "компании соревнуются"
× Только конкретика, цифры, факты, последствия
× Эмодзи в меру (1-2 максимум в начале)
× Пиши на русском, простым языком, без хайпа ради хайпа

ХОРОШИЕ ПРИМЕРЫ ПОДАЧИ:
✓ "DeepMind обучила ИИ предсказывать погоду точнее метеорологов"
✓ "Новая модель обходит GPT-4 в математике при 10x меньших затратах"
✓ "Google снова догоняет — или на этот раз обгонит?"
✓ "Сколько стартапов похоронит это обновление?"

Если новость — мусор/реклама/не про технологии, ответь: SKIP

ПОСТ:"""

    for attempt in range(config.groq_max_retries):
        try:
            await asyncio.sleep(0.5)
            
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=random.choice(GROQ_MODELS),
                temperature=0.7,
                max_tokens=1100,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()

            if "SKIP" in text.upper()[:15]:
                logger.info("  ⏭️ LLM: SKIP")
                return None

            if len(text) < config.min_post_length:
                logger.warning(f"  ⚠️ Короткий текст ({len(text)} симв.), повтор...")
                continue

            water = ["стоит отметить", "важно понимать", "интересно, что", 
                    "давайте разберёмся", "не секрет", "очевидно, что"]
            if any(w in text.lower() for w in water):
                logger.warning("  ⚠️ Обнаружена вода, повтор...")
                continue

            topic = Topic.detect(f"{article.title} {article.summary}")
            hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])
            
            cta = "\n\n🔥 — огонь  |  🗿 — ну такое  |  ⚡ — интересно"
            source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
            
            final = f"{text}{cta}\n\n{hashtags}{source_link}"

            if len(final) > config.caption_limit:
                excess = len(final) - config.caption_limit + 20
                text = text[:-excess]
                for p in ['. ', '! ', '? ']:
                    idx = text.rfind(p)
                    if idx > len(text) * 0.6:
                        text = text[:idx+1]
                        break
                final = f"{text}{cta}\n\n{hashtags}{source_link}"

            logger.info(f"  ✅ Готово: {len(text)} символов | Тема: {topic}")
            return final
            
        except Exception as e:
            delay = exponential_backoff(attempt)
            logger.error(f"  ❌ Groq ошибка (попытка {attempt+1}/{config.groq_max_retries}): {e}")
            logger.info(f"  ⏳ Повтор через {delay:.1f}s...")
            await asyncio.sleep(delay)

    return None

# ====================== КАРТИНКИ ======================
async def generate_image(title: str, topic: str = Topic.GENERAL) -> Optional[str]:
    logger.info(f"  🎨 Генерация картинки для темы: {topic}")
    
    clean_title = re.sub(r'[^\w\s]', '', title)[:50]
    style = Topic.get_image_style(topic)
    prompt = f"{style}, {clean_title}, high quality, 4k, sharp focus"
    
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        f"?width=1024&height=1024&nologo=true&seed={random.randint(1,99999)}"
    )
    
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                    if resp.status != 200:
                        logger.warning(f"  ⚠️ HTTP {resp.status}, попытка {attempt+1}")
                        continue
                    
                    data = await resp.read()
                    
                    if len(data) < 10000:
                        logger.warning(f"  ⚠️ Слишком маленький файл ({len(data)} bytes)")
                        continue
                    
                    fname = f"img_{random.randint(1000,9999)}.jpg"
                    with open(fname, "wb") as f:
                        f.write(data)
                    
                    logger.info(f"  ✅ Картинка сохранена: {fname} ({len(data)//1024}KB)")
                    return fname
                    
        except asyncio.TimeoutError:
            logger.warning(f"  ⚠️ Таймаут, попытка {attempt+1}/3")
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"  ⚠️ Ошибка генерации ({attempt+1}/3): {e}")
            await asyncio.sleep(2)
    
    logger.warning("  ❌ Не удалось сгенерировать картинку")
    return None

# ====================== ПУБЛИКАЦИЯ ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    topic = Topic.detect(f"{article.title} {article.summary}")
    
    # Проверка разнообразия
    diversity_ok, diversity_reason = posted.check_diversity_requirement(topic)
    if not diversity_ok:
        posted.log_rejected(article, diversity_reason)
        logger.info(f"  ⏭️ Пропуск (требуется разнообразие): {diversity_reason}")
        return False
    
    # LLM-проверка дубликатов
    recent_posts = posted.get_recent_posts(3)
    is_llm_dup, llm_reason = posted.llm_duplicate_check(article, recent_posts)
    if is_llm_dup:
        posted.log_rejected(article, llm_reason)
        logger.info(f"  ⏭️ LLM определил как дубликат")
        return False
    
    # Генерация изображения
    img = await generate_image(article.title, topic)
    
    try:
        if img and os.path.exists(img):
            await bot.send_photo(config.channel_id, FSInputFile(img), caption=text)
            os.remove(img)
        else:
            await bot.send_message(config.channel_id, text, disable_web_page_preview=False)
        
        posted.add(article, topic)
        
        logger.info(f"✅ ОПУБЛИКОВАНО [{topic.upper()}]: {article.title[:50]}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Telegram ошибка: {e}")
        if img and os.path.exists(img):
            try:
                os.remove(img)
            except:
                pass
        return False

# ====================== MAIN ======================
async def main():
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК AI-POSTER v3.0 (SQLite + Усиленная дедупликация)")
    logger.info("=" * 50)
    
    posted = PostedManager(config.db_file)
    
    try:
        posted.cleanup(config.retention_days)
        
        # Выводим статистику
        stats = posted.get_stats()
        logger.info(f"📊 Статистика БД: {stats['total_posted']} posted, {stats['total_rejected']} rejected")
        
        raw_articles = await load_all_feeds(posted)
        candidates = filter_articles(raw_articles, posted)
        
        if not candidates:
            logger.info("📭 Нет подходящих новостей")
            return

        for article in candidates[:20]:
            # Финальная проверка на дубликат перед обработкой
            is_dup, reason = posted.is_duplicate(article.link, article.title, article.summary)
            if is_dup:
                posted.log_rejected(article, f"FINAL_CHECK: {reason}")
                logger.debug(f"  Пропуск (финальная проверка): {article.title[:40]}")
                continue
            
            is_similar, reason = posted.is_too_similar_to_recent(article.title, article.summary)
            if is_similar:
                posted.log_rejected(article, f"FINAL_RECENT: {reason}")
                logger.debug(f"  Пропуск (похоже на недавние): {article.title[:40]}")
                continue
            
            summary = await generate_summary(article)
            if not summary:
                posted.log_rejected(article, "LLM_GENERATION_FAILED")
                continue
            
            if await post_article(article, summary, posted):
                logger.info("\n🏁 Готово! Скрипт завершён.")
                break
            
            await asyncio.sleep(3)
        else:
            logger.info("😔 Не удалось опубликовать ни одной статьи")
    
    finally:
        posted.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())































































































































































































































































