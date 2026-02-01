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
        IMAGE_GEN: "#Midjourney #DALLE #StableDiffusion #генерация",
        ROBOTICS: "#роботы #Humanoid #робототехника",
        HARDWARE: "#NVIDIA #GPU #чипы #железо",
        REGULATION: "#регулирование #безопасность #этика",
        RESEARCH: "#исследования #наука #DeepMind",
        AGENTS: "#AIAgents #Агенты #AutonomousAI",
        CODING: "#Cursor #GitHubCopilot #AIкодинг",
        GENERAL: "#AI #нейросети #ИИ"
    }
    
    IMAGE_STYLES = {
        LLM: [
            "clean minimalist illustration, chat interface, soft blue and white gradient, modern UI design, professional",
            "friendly robot assistant illustration, soft colors, white background, cute character design",
            "abstract conversation bubbles, flowing shapes, light blue tones, editorial style illustration",
            "modern flat design, speech bubbles and text symbols, pastel colors, tech magazine cover",
        ],
        IMAGE_GEN: [
            "artistic watercolor illustration, creative palette, splashes of color, gallery aesthetic",
            "paintbrush and canvas artistic concept, warm colors, creative studio atmosphere",
            "abstract art composition, flowing colors, creative expression, museum quality",
            "digital art creation concept, colorful gradients, artistic tools, inspiring atmosphere",
        ],
        ROBOTICS: [
            "technical blueprint illustration, soft gray background, precise mechanical drawings, engineering style",
            "friendly humanoid robot, soft studio lighting, white background, product photography style",
            "isometric robot illustration, clean lines, soft shadows, modern industrial design",
            "robotic arm in laboratory setting, clean environment, professional photography style",
        ],
        HARDWARE: [
            "product photography of tech hardware, studio lighting, reflective surfaces, premium feel",
            "clean circuit board illustration, green and gold tones, technical precision, macro style",
            "isometric computer chip illustration, metallic textures, soft gradients, professional",
            "modern data center visualization, clean rows of servers, soft blue lighting, corporate",
        ],
        REGULATION: [
            "corporate illustration, scales of justice with tech elements, muted blue tones, professional",
            "formal document and gavel illustration, clean design, government style, serious tone",
            "handshake between human and robot, diplomatic setting, soft neutral colors, editorial",
            "policy document with AI symbols, clean infographic style, trustworthy blue palette",
        ],
        RESEARCH: [
            "scientific laboratory illustration, clean white environment, research equipment, academic",
            "brain and neural connections visualization, soft purple and blue, medical illustration style",
            "scientist working with data, modern lab setting, clean aesthetic, educational",
            "abstract knowledge graph, interconnected nodes, soft colors, scientific visualization",
        ],
        AGENTS: [
            "autonomous agent illustration, interconnected nodes, soft purple and blue, futuristic but clean",
            "ai agent workflow diagram, clean design, soft gradients, professional infographic",
            "multiple ai agents collaborating, isometric illustration, soft colors, modern tech",
            "autonomous system visualization, flowing data streams, soft blue tones, editorial",
        ],
        CODING: [
            "clean code editor interface, syntax highlighting, dark theme with soft colors, developer aesthetic",
            "ai coding assistant illustration, code snippets floating, soft blue and purple, modern",
            "programmer workspace with ai, clean desk setup, soft lighting, professional",
            "abstract code visualization, flowing lines of code, soft gradients, tech magazine",
        ],
        GENERAL: [
            "modern flat illustration, geometric shapes, pastel gradient colors, editorial magazine style",
            "clean tech illustration, simple icons, white background, professional presentation",
            "isometric technology concept, soft shadows, modern design, business friendly",
            "minimalist abstract design, flowing lines, soft blue and white, corporate clean",
        ],
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["cursor", "copilot", "replit", "v0", "bolt.new", "coding", "programming", "developer"]):
            return Topic.CODING
        if any(x in t for x in ["agent", "autonomous", "crewai", "autogen", "langchain agent"]):
            return Topic.AGENTS
        if any(x in t for x in ["gpt", "chatgpt", "claude", "gemini", "llama", "grok", "llm", "o3", "o1", "deepseek"]):
            return Topic.LLM
        if any(x in t for x in ["midjourney", "dall-e", "stable diffusion", "flux", "sora", "imagen"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics", "optimus", "figure", "unitree", "agility"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "h100", "h200", "blackwell", "gpu", "cuda", "chip", "hardware"]):
            return Topic.HARDWARE
        if any(x in t for x in ["regulation", "safety", "alignment", "ethics", "policy", "governance"]):
            return Topic.REGULATION
        if any(x in t for x in ["research", "paper", "study", "breakthrough", "discovery", "arxiv"]):
            return Topic.RESEARCH
        return Topic.GENERAL
    
    @staticmethod
    def get_image_style(topic: str) -> str:
        styles = Topic.IMAGE_STYLES.get(topic, Topic.IMAGE_STYLES[Topic.GENERAL])
        return random.choice(styles)

# ====================== URL НОРМАЛИЗАЦИЯ ======================
UTM_PARAMS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
              'fbclid', 'gclid', 'twclid', 'li_fat_id', 'mc_cid', 'mc_eid',
              'utm_id', 'utm_source_platform', 'utm_creative_format', 'utm_marketing_tactic']

def normalize_url(url: str) -> str:
    """
    Усиленная нормализация URL:
    - Удаление UTM-параметров
    - Удаление якорей
    - Удаление trailing slashes
    - Приведение к lowercase
    - Оставляем только scheme://host/path
    """
    if not url:
        return ""
    try:
        url = url.strip().lower()
        parsed = urlparse(url)
        
        # Удаляем UTM и другие tracking параметры
        query_params = parse_qs(parsed.query)
        filtered_params = {k: v for k, v in query_params.items() 
                          if k.lower() not in UTM_PARAMS}
        
        # Формируем нормализованный URL
        domain = parsed.netloc.replace("www.", "")
        path = parsed.path.rstrip("/")
        
        if filtered_params:
            new_query = urlencode(filtered_params, doseq=True)
            return f"{parsed.scheme}://{domain}{path}?{new_query}"
        else:
            return f"{parsed.scheme}://{domain}{path}"
    except Exception:
        # Fallback: базовая очистка
        url = url.lower().split('#')[0].split('?')[0].rstrip('/')
        return url

def get_domain(url: str) -> str:
    """Извлекает нормализованный домен из URL"""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower().replace("www.", "")
    except:
        return ""

def get_title_signature(title: str) -> str:
    """Возвращает сигнатуру заголовка (первая половина слов)"""
    words = re.findall(r'\w+', title.lower())
    half = max(1, len(words) // 2)
    return ' '.join(words[:half])

# ====================== ХЕШИРОВАНИЕ ======================
def get_content_hash(text: str) -> str:
    """MD5 хеш нормализованного контента"""
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized[:2000].encode()).hexdigest()

def get_summary_hash(summary: str) -> str:
    """MD5 хеш нормализованного summary"""
    if not summary:
        return ""
    # Удаляем пунктуацию и лишние пробелы
    normalized = re.sub(r'[^\w\s]', '', summary.lower())
    normalized = re.sub(r'\s+', ' ', normalized.strip())
    return hashlib.md5(normalized.encode()).hexdigest()

# ====================== ПОХОЖЕСТЬ ТЕКСТА ======================
def calculate_similarity(text1: str, text2: str) -> float:
    """Схожесть двух строк (0.0 - 1.0) с использованием SequenceMatcher"""
    if not text1 or not text2:
        return 0.0
    return difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def ngram_similarity(text1: str, text2: str, n: int = 3) -> float:
    """Схожесть по n-граммам"""
    if not text1 or not text2:
        return 0.0
    
    def get_ngrams(text, n):
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        words = text.split()
        return set([' '.join(words[i:i+n]) for i in range(len(words)-n+1)])
    
    ngrams1 = get_ngrams(text1, n)
    ngrams2 = get_ngrams(text2, n)
    
    if not ngrams1 or not ngrams2:
        return 0.0
    
    intersection = ngrams1 & ngrams2
    union = ngrams1 | ngrams2
    
    return len(intersection) / len(union) if union else 0.0

# ====================== TF-IDF SIMILARITY ======================
class TFIDFCalculator:
    """Простой TF-IDF калькулятор для сравнения текстов"""
    
    @staticmethod
    def tokenize(text: str) -> List[str]:
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        words = text.split()
        # Удаляем стоп-слова
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                      'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                      'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                      'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
                      'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
                      'through', 'during', 'before', 'after', 'above', 'below',
                      'between', 'under', 'and', 'but', 'or', 'yet', 'so', 'if',
                      'because', 'although', 'though', 'while', 'where', 'when',
                      'that', 'which', 'who', 'whom', 'whose', 'what', 'this',
                      'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
                      'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his', 'her',
                      'its', 'our', 'their', 'mine', 'yours', 'hers', 'ours', 'theirs'}
        return [w for w in words if len(w) > 2 and w not in stop_words]
    
    @staticmethod
    def compute_tf(tokens: List[str]) -> Dict[str, float]:
        token_counts = Counter(tokens)
        total = len(tokens)
        return {token: count / total for token, count in token_counts.items()} if total else {}
    
    @staticmethod
    def compute_idf(documents: List[List[str]]) -> Dict[str, float]:
        idf = {}
        total_docs = len(documents)
        all_tokens = set()
        for doc in documents:
            all_tokens.update(doc)
        
        for token in all_tokens:
            doc_count = sum(1 for doc in documents if token in doc)
            idf[token] = math.log(total_docs / (doc_count + 1)) + 1
        
        return idf
    
    @classmethod
    def cosine_similarity(cls, text1: str, text2: str, context_texts: List[str] = None) -> float:
        """Вычисляет косинусное сходство между двумя текстами"""
        tokens1 = cls.tokenize(text1)
        tokens2 = cls.tokenize(text2)
        
        if not tokens1 or not tokens2:
            return 0.0
        
        # Собираем документы для IDF
        documents = [tokens1, tokens2]
        if context_texts:
            documents.extend([cls.tokenize(t) for t in context_texts])
        
        idf = cls.compute_idf(documents)
        
        tf1 = cls.compute_tf(tokens1)
        tf2 = cls.compute_tf(tokens2)
        
        # TF-IDF векторы
        all_terms = set(tf1.keys()) | set(tf2.keys())
        vec1 = [tf1.get(term, 0) * idf.get(term, 1) for term in all_terms]
        vec2 = [tf2.get(term, 0) * idf.get(term, 1) for term in all_terms]
        
        # Косинусное сходство
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(a * a for a in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)

# ====================== ИЗВЛЕЧЕНИЕ СУЩНОСТЕЙ ======================
def extract_key_entities(text: str) -> Set[str]:
    """Извлекает ключевые сущности из текста с fuzzy matching"""
    text_lower = text.lower()
    found = set()
    
    for entity in KEY_ENTITIES:
        # Точное совпадение
        if entity in text_lower:
            normalized = entity.replace("-", " ").replace("_", " ")
            found.add(normalized)
            continue
        
        # Fuzzy matching для вариаций
        entity_words = entity.split()
        if len(entity_words) == 1:
            # Для однословных сущностей проверяем границы слов
            pattern = r'\b' + re.escape(entity) + r'\b'
            if re.search(pattern, text_lower):
                found.add(entity)
    
    return found

def fuzzy_entity_match(entities1: Set[str], entities2: Set[str]) -> float:
    """Fuzzy matching между наборами сущностей"""
    if not entities1 or not entities2:
        return 0.0
    
    matches = 0
    for e1 in entities1:
        for e2 in entities2:
            # Прямое совпадение
            if e1 == e2:
                matches += 1
                break
            # Частичное совпадение (одно содержит другое)
            if e1 in e2 or e2 in e1:
                matches += 0.8
                break
            # Высокая схожесть
            if calculate_similarity(e1, e2) > 0.85:
                matches += 0.7
                break
    
    return matches / max(len(entities1), len(entities2))

# ====================== SQLITE MANAGER ======================
class PostedManager:
    """SQLite-based менеджер с атомарными операциями и advisory locks"""
    
    def __init__(self, db_file: str = "posted_articles.db"):
        self.db_file = db_file
        self._local = threading.local()
        self._init_db()
        self._acquire_lock()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Получает thread-local соединение"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_file, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    def _init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_file)
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
        conn.close()
        logger.info("📚 База данных инициализирована")
    
    def _acquire_lock(self):
        """SQLite advisory lock для предотвращения параллельного запуска"""
        self.lock_conn = sqlite3.connect(self.db_file)
        try:
            # Пробуем получить advisory lock
            self.lock_conn.execute("BEGIN IMMEDIATE")
            logger.info("🔒 Advisory lock получен")
        except sqlite3.OperationalError:
            logger.warning("⚠️ Не удалось получить lock, другой процесс работает")
            raise SystemExit(0)
    
    def _release_lock(self):
        """Освобождает advisory lock"""
        try:
            if hasattr(self, 'lock_conn') and self.lock_conn:
                self.lock_conn.close()
                logger.info("🔓 Advisory lock освобождён")
        except:
            pass
    
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
        context_texts = [row[1] for row in recent_posts if row[1]]
        
        for post in recent_posts:
            post_title, post_summary, post_topic, saved_entities_json = post
            
            # Проверка похожести заголовков
            if post_title:
                sim = calculate_similarity(title, post_title)
                if sim > config.recent_similarity_threshold:
                    return True, f"RECENT_TITLE_SIM: {sim:.2f}"
            
            # TF-IDF similarity для summary
            if post_summary and summary:
                tfidf_sim = TFIDFCalculator.cosine_similarity(summary, post_summary, context_texts)
                if tfidf_sim > config.tfidf_similarity_threshold:
                    return True, f"TFIDF_SIMILARITY: {tfidf_sim:.2f}"
            
            # Проверка темы и сущностей
            post_entities = set(json.loads(saved_entities_json)) if saved_entities_json else set()
            
            if detected_topic == post_topic and post_entities:
                common = new_entities & post_entities
                if len(common) >= config.min_entity_distance:
                    return True, f"RECENT_TOPIC_ENTITIES: {detected_topic}"
        
        return False, ""
    
    def check_diversity_requirement(self, proposed_topic: str) -> Tuple[bool, str]:
        """
        Проверяет требование разнообразия:
        если последние 3 поста про одну тему, следующий должен быть другой
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('SELECT topic FROM posted_articles '
                      'ORDER BY posted_date DESC LIMIT ?', (config.diversity_window,))
        recent_topics = [row[0] for row in cursor.fetchall()]
        
        if len(recent_topics) < config.diversity_window:
            return True, ""
        
        # Проверяем, все ли последние посты на одну тему
        topic_counts = Counter(recent_topics)
        dominant_topic, count = topic_counts.most_common(1)[0]
        
        if count >= config.diversity_window and proposed_topic == dominant_topic:
            return False, f"DIVERSITY_REQUIRED: последние {config.diversity_window} постов про {dominant_topic}"
        
        return True, ""
    
    def llm_duplicate_check(self, article: Article, recent_posts: List[dict]) -> Tuple[bool, str]:
        """
        LLM-проверка: отправляет новую статью + последние 3 поста в LLM
        с вопросом "Это дубликат? YES/NO"
        """
        if not recent_posts:
            return False, ""
        
        # Формируем контекст
        context = "НЕДАВНИЕ ПОСТЫ:\n\n"
        for i, post in enumerate(recent_posts[:3], 1):
            context += f"{i}. {post.get('title', '')}\n"
            context += f"   Сущности: {', '.join(post.get('entities', []))}\n\n"
        
        prompt = f"""Ты — редактор новостного канала про ИИ. Твоя задача — определить, является ли новая статья дубликатом уже опубликованных.

{context}

НОВАЯ СТАТЬЯ:
Заголовок: {article.title}
Краткое содержание: {article.summary[:500]}
Источник: {article.source}

ПРАВИЛА:
- Дубликат = статья про ТОТ ЖЕ новостной событие/анонс/релиз
- Разные источники про одно событие = дубликат
- Перефразированный заголовок, но та же суть = дубликат
- Другой аспект той же темы = НЕ дубликат
- Разные модели/продукты одной компании = НЕ дубликат

Ответь ТОЛЬКО: YES (если дубликат) или NO (если уникальная новость)

Ответ:"""
        
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.choices[0].message.content.strip().upper()
            
            if "YES" in answer:
                return True, "LLM_DUPLICATE_CHECK"
            return False, ""
            
        except Exception as e:
            logger.warning(f"⚠️ LLM duplicate check failed: {e}")
            return False, ""  # При ошибке LLM пропускаем
    
    def log_rejected(self, article: Article, reason: str, duplicate_of: str = None, 
                     similarity_score: float = None):
        """Логирует отклонённую статью в отдельную таблицу"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO rejected_articles 
                (url, norm_url, title, summary, source, rejection_reason, duplicate_of, similarity_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article.link,
                normalize_url(article.link),
                article.title[:200],
                article.summary[:1000] if article.summary else None,
                article.source,
                reason,
                duplicate_of,
                similarity_score
            ))
            conn.commit()
            logger.info(f"📝 Залогировано rejected: {reason[:50]}")
        except Exception as e:
            logger.error(f"Ошибка логирования rejected: {e}")
    
    def add(self, article: Article, topic: str = Topic.GENERAL):
        """Добавляет статью в историю"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        norm_url = normalize_url(article.link)
        domain = get_domain(article.link)
        title_sig = get_title_signature(article.title)
        summary_hash = get_summary_hash(article.summary)
        content_hash = get_content_hash(article.summary)
        full_text = f"{article.title} {article.summary}".strip()
        entities = list(extract_key_entities(full_text))
        
        try:
            cursor.execute('''
                INSERT INTO posted_articles 
                (url, norm_url, domain, title, title_signature, summary, content_hash, summary_hash, 
                 entities, topic, source, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article.link,
                norm_url,
                domain,
                article.title[:200],
                title_sig,
                article.summary[:2000] if article.summary else None,
                content_hash,
                summary_hash,
                json.dumps(entities),
                topic,
                article.source,
                article.published.isoformat() if article.published else None
            ))
            conn.commit()
            logger.info(f"💾 [{topic.upper()}] {article.title[:45]}... | Сущности: {entities if entities else 'нет'}")
        except sqlite3.IntegrityError:
            logger.warning(f"⚠️ IntegrityError (дубликат URL): {article.title[:40]}")
        except Exception as e:
            logger.error(f"Ошибка добавления статьи: {e}")
    
    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        """Возвращает последние N постов"""
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
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            DELETE FROM posted_articles 
            WHERE posted_date < datetime("now", "-? days")
        ''', (days,))
        
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
    
    def __del__(self):
        self._release_lock()

import threading  # Добавляем импорт для thread-local storage

# ====================== HELPERS ======================
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def ai_relevance(text: str) -> float:
    lower = text.lower()
    matches = sum(1 for kw in AI_KEYWORDS if kw in lower)
    return min(matches / 3.0, 1.0)

# ====================== RSS LOADER ======================
async def fetch_feed(session: aiohttp.ClientSession, url: str, source: str, 
                     posted: PostedManager) -> List[Article]:
    # Добавляем jitter к timeout
    timeout = config.rss_timeout + random.uniform(0, config.rss_jitter)
    
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                logger.warning(f"{source}: HTTP {resp.status}")
                return []
            text = await resp.text()
    except asyncio.TimeoutError:
        logger.warning(f"{source}: Timeout after {timeout:.1f}s")
        return []
    except Exception as e:
        logger.warning(f"{source}: {e}")
        return []

    try:
        feed = feedparser.parse(text)
    except Exception as e:
        logger.warning(f"{source}: Parse error {e}")
        return []

    articles = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=config.max_article_age_hours)
    
    for entry in feed.entries[:25]:
        link = entry.get("link", "").strip()
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1500]

        if not link or len(title) < 15:
            continue
        
        # Проверка длины summary
        if len(summary) < config.min_summary_length:
            logger.debug(f"  Пропуск (короткий summary {len(summary)}): {title[:40]}")
            continue
        
        # Парсим дату публикации
        published = datetime.now(timezone.utc)
        date_found = False
        for df in ["published", "updated", "created", "pubDate"]:
            ds = entry.get(df)
            if ds:
                try:
                    parsed = feedparser._parse_date(ds)
                    if parsed:
                        published = datetime(*parsed[:6], tzinfo=timezone.utc)
                        date_found = True
                        break
                except:
                    pass
        
        # Проверка возраста статьи
        if date_found and published < cutoff_time:
            logger.debug(f"  Пропуск (устарело {published}): {title[:40]}")
            continue
        
        # Проверка на дубликат
        is_dup, reason = posted.is_duplicate(link, title, summary)
        if is_dup:
            logger.debug(f"  Пропуск (дубликат: {reason}): {title[:40]}")
            continue

        articles.append(Article(
            title=title,
            summary=summary,
            link=link,
            source=source,
            published=published
        ))

    return articles

async def load_all_feeds(posted: PostedManager) -> List[Article]:
    logger.info("🔄 Загрузка RSS...")
    
    conn = aiohttp.TCPConnector(limit=30)
    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as session:
        tasks = [fetch_feed(session, url, name, posted) for url, name in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    for i, res in enumerate(results):
        source_name = RSS_FEEDS[i][1]
        if isinstance(res, list) and res:
            all_articles.extend(res)
            logger.info(f"  ✓ {source_name}: {len(res)} новых")
        elif isinstance(res, Exception):
            logger.error(f"  ✗ {source_name}: {res}")

    logger.info(f"📊 Всего кандидатов: {len(all_articles)}")
    return all_articles

# ====================== FILTER ======================
def filter_articles(articles: List[Article], posted: PostedManager) -> List[Article]:
    candidates = []
    
    recent_stats = posted.get_recent_topics_stats()
    logger.info(f"📊 Последние темы: {recent_stats}")
    
    for a in articles:
        text = f"{a.title} {a.summary}".lower()
        
        if any(p in text for p in BAD_PHRASES):
            posted.log_rejected(a, "BAD_PHRASES")
            continue
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            posted.log_rejected(a, "EXCLUDE_KEYWORDS")
            continue
        if not any(kw in text for kw in AI_KEYWORDS):
            posted.log_rejected(a, "NO_AI_KEYWORDS")
            continue
        if ai_relevance(text) < 0.4:
            posted.log_rejected(a, "LOW_AI_RELEVANCE")
            continue
        
        # Проверка на схожесть с недавними постами
        is_similar, reason = posted.is_too_similar_to_recent(a.title, a.summary)
        if is_similar:
            posted.log_rejected(a, f"TOO_SIMILAR_RECENT: {reason}")
            logger.debug(f"  Пропуск (слишком похоже на недавние): {a.title[:40]}")
            continue
        
        candidates.append(a)

    candidates.sort(key=lambda x: x.published, reverse=True)
    
    logger.info(f"🎯 После фильтров: {len(candidates)} статей")
    return candidates

# ====================== ГЕНЕРАТОР ПОСТОВ ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
]

def exponential_backoff(attempt: int) -> float:
    """Экспоненциальная задержка с jitter"""
    delay = min(
        config.groq_base_delay * (2 ** attempt) + random.uniform(0, 1),
        config.groq_max_delay
    )
    return delay

async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация: {article.title[:55]}...")
    
    prompt = f"""Ты — редактор крупного русскоязычного Telegram-канала про ИИ и технологии.

НОВОСТЬ:
Заголовок: {article.title}
Текст: {article.summary[:2200]}
Источник: {article.source}

ЗАДАЧА: Напиши пост на русском языке.

СТРУКТУРА (обязательно):
1. 🔥 ЗАГОЛОВОК — цепляющий, с эмодзи, отражает суть
2. ЧТО СЛУЧИЛОСЬ — 3-4 предложения с фактами (кто, что, когда, цифры)
3. ПОЧЕМУ ВАЖНО — 2 предложения о влиянии на индустрию/пользователей  
4. ВЫВОД — острый комментарий или провокационный вопрос

ТРЕБОВАНИЯ:
- Длина: 600-850 символов (ОБЯЗАТЕЛЬНО)
- Только факты, никакой воды
- Конкретика: названия компаний, цифры, даты

ЗАПРЕЩЕНО:
- Фразы: "стоит отметить", "важно понимать", "интересно что", "друзья"
- Шаблонные вопросы типа "Что думаете?"
- Пустые обобщения без фактов

ХОРОШИЕ ВОПРОСЫ:
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

    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())






























































































































































































































































