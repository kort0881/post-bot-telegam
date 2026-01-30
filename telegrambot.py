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
from datetime import datetime, timezone
from typing import List, Set, Optional
from urllib.parse import urlparse, quote
from dataclasses import dataclass, field

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from groq import Groq

# Для блокировки файлов (Linux/Mac)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

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
        self.retention_days = int(os.getenv("RETENTION_DAYS", "30"))
        self.caption_limit = 1024
        self.posted_file = "posted_articles.json"
        
        self.similarity_threshold = 0.60  # Порог похожести заголовков
        self.entity_overlap_threshold = 0.55  # Порог совпадения сущностей
        self.min_post_length = 500
        
        # 🆕 НОВЫЕ ПАРАМЕТРЫ ДЛЯ РАЗНООБРАЗИЯ
        self.recent_posts_check = 5  # Проверять последние N постов на разнообразие
        self.recent_similarity_threshold = 0.45  # Более строгий порог для последних постов
        self.min_entity_distance = 2  # Мин. количество уникальных сущностей

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
    # Основные
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED"),
    
    # 🆕 ДОПОЛНИТЕЛЬНЫЕ ИСТОЧНИКИ
    ("https://www.artificialintelligence-news.com/feed/", "AI News"),
    ("https://hai.stanford.edu/news/rss.xml", "Stanford HAI"),
    ("https://deepmind.google/blog/rss.xml", "DeepMind Blog"),
    ("https://openai.com/blog/rss/", "OpenAI Blog"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://www.marktechpost.com/feed/", "MarkTechPost"),
    ("https://syncedreview.com/feed/", "Synced AI"),
    ("https://news.ycombinator.com/rss", "Hacker News"),  # Много AI-новостей
    ("https://www.unite.ai/feed/", "Unite.AI"),
    ("https://analyticsindiamag.com/feed/", "AIM"),
]

# ====================== КЛЮЧЕВЫЕ СЛОВА ======================
AI_KEYWORDS = [
    "ai ", " ai", "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model", "gpt", "chatgpt", "claude",
    "gemini", "grok", "llama", "mistral", "qwen", "deepseek", "midjourney",
    "dall-e", "stable diffusion", "sora", "groq", "openai", "anthropic",
    "deepmind", "hugging face", "nvidia", "agi", "transformer", "generative",
    "agents", "reasoning", "multimodal", "fine-tuning", "rlhf"
]

EXCLUDE_KEYWORDS = [
    "stock price", "ipo", "earnings call", "quarterly results", "dividend",
    "market cap", "wall street", "ps5", "xbox", "nintendo", "game review",
    "netflix", "movie review", "box office", "trailer", "tesla stock",
    "bitcoin", "crypto", "blockchain", "nft", "ethereum", "election",
    "trump", "biden", "congress", "senate"
]

BAD_PHRASES = ["sponsored", "partner content", "advertisement", "black friday", "deal alert"]

# ====================== КЛЮЧЕВЫЕ СУЩНОСТИ ДЛЯ ДЕТЕКЦИИ ДУБЛЕЙ ======================
KEY_ENTITIES = [
    # Компании
    "openai", "google", "meta", "microsoft", "anthropic", "nvidia", "apple",
    "amazon", "deepmind", "hugging face", "stability ai", "midjourney",
    "mistral", "cohere", "perplexity", "runway", "pika", "character ai",
    "inflection", "xai", "baidu", "alibaba", "tencent", "bytedance",
    
    # Продукты и модели
    "gpt-4", "gpt-5", "gpt-4o", "gpt-4.5", "chatgpt", "claude", "claude 3",
    "gemini", "gemini 2", "llama", "llama 3", "mistral", "mixtral",
    "copilot", "dall-e", "dall-e 3", "sora", "stable diffusion", "flux",
    "midjourney v6", "runway gen", "firefly", "imagen",
    
    # Ключевые темы
    "linux foundation", "agentic", "ai agent", "agi", "asi",
    "regulation", "safety", "alignment", "open source", "open-source",
    "robotics", "humanoid", "boston dynamics", "figure", "optimus",
    
    # Технологии
    "transformer", "diffusion", "multimodal", "reasoning", "chain of thought",
    "fine-tuning", "rlhf", "inference", "training", "benchmark"
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
    GENERAL = "general"
    
    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #DALLE #StableDiffusion #генерация",
        ROBOTICS: "#роботы #Humanoid #робототехника",
        HARDWARE: "#NVIDIA #GPU #чипы #железо",
        REGULATION: "#регулирование #безопасность #этика",
        RESEARCH: "#исследования #наука #DeepMind",
        GENERAL: "#AI #нейросети #ИИ"
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["gpt", "chatgpt", "claude", "gemini", "llama", "grok", "llm"]):
            return Topic.LLM
        if any(x in t for x in ["midjourney", "dall-e", "stable diffusion", "flux", "sora"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics", "optimus", "figure"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "h100", "h200", "blackwell", "gpu", "cuda"]):
            return Topic.HARDWARE
        if any(x in t for x in ["regulation", "safety", "alignment", "ethics", "policy"]):
            return Topic.REGULATION
        if any(x in t for x in ["research", "paper", "study", "breakthrough", "discovery"]):
            return Topic.RESEARCH
        return Topic.GENERAL

# ====================== HELPERS ======================
def normalize_url(url: str) -> str:
    """Нормализация URL для сравнения"""
    if not url:
        return ""
    try:
        url = url.strip()
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{domain}{path}"
    except:
        return url.split("?")[0].split("#")[0]

def calculate_similarity(text1: str, text2: str) -> float:
    """Схожесть двух строк (0.0 - 1.0)"""
    return difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def extract_key_entities(text: str) -> Set[str]:
    """Извлекает ключевые сущности из текста"""
    text_lower = text.lower()
    found = set()
    
    for entity in KEY_ENTITIES:
        if entity in text_lower:
            # Нормализуем некоторые варианты
            normalized = entity.replace("-", " ").replace("_", " ")
            found.add(normalized)
    
    return found

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

def get_content_hash(text: str) -> str:
    """MD5 хеш нормализованного контента"""
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    # Берём первые 500 символов для хеша (достаточно для уникальности)
    return hashlib.md5(normalized[:500].encode()).hexdigest()[:16]

# ====================== POSTED MANAGER ======================
class PostedManager:
    def __init__(self, file="posted_articles.json"):
        self.file = file
        self.lock_file = file + ".lock"
        self.data: List[dict] = []
        self.urls: Set[str] = set()
        self.titles: List[str] = []
        self.content_hashes: Set[str] = set()
        self.topic_entities: List[Set[str]] = []
        self.topics: List[str] = []  # 🆕 Для отслеживания тем
        self._lock_fd = None
        
        self._acquire_lock()
        self._load()

    def _acquire_lock(self):
        """Блокировка для предотвращения параллельного запуска"""
        if not HAS_FCNTL:
            # Windows fallback
            if os.path.exists(self.lock_file):
                try:
                    age = datetime.now().timestamp() - os.path.getmtime(self.lock_file)
                    if age < 600:
                        logger.warning("⚠️ Другой экземпляр работает. Выход.")
                        raise SystemExit(0)
                except OSError:
                    pass
            with open(self.lock_file, 'w') as f:
                f.write(str(os.getpid()))
            return
        
        # Linux/Mac
        self._lock_fd = open(self.lock_file, 'w')
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
        except BlockingIOError:
            logger.warning("⚠️ Скрипт уже запущен. Выход.")
            raise SystemExit(0)

    def _release_lock(self):
        try:
            if HAS_FCNTL and self._lock_fd:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
        except:
            pass

    def _load(self):
        if not os.path.exists(self.file):
            self._save()
            return
        
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            self._rebuild_caches()
            logger.info(f"📚 Загружено {len(self.data)} статей из истории")
        except Exception as e:
            logger.error(f"Ошибка загрузки истории: {e}")
            self.data = []

    def _rebuild_caches(self):
        """Перестраивает все кэши из self.data"""
        self.urls.clear()
        self.titles.clear()
        self.content_hashes.clear()
        self.topic_entities.clear()
        self.topics.clear()
        
        for item in self.data:
            # URL
            url = item.get("url", "")
            if url:
                self.urls.add(normalize_url(url))
            
            # Title
            title = item.get("title", "")
            if title:
                self.titles.append(title)
            else:
                self.titles.append("")
            
            # Entities
            saved_entities = item.get("entities", [])
            if saved_entities:
                self.topic_entities.append(set(saved_entities))
            elif title:
                self.topic_entities.append(extract_key_entities(title))
            else:
                self.topic_entities.append(set())
            
            # Content hash
            chash = item.get("content_hash", "")
            if chash:
                self.content_hashes.add(chash)
            
            # 🆕 Topic
            topic = item.get("topic", Topic.GENERAL)
            self.topics.append(topic)

    def _save(self):
        """Атомарное сохранение"""
        try:
            dir_name = os.path.dirname(self.file) or '.'
            fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=dir_name)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            shutil.move(tmp_path, self.file)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")

    def is_duplicate(self, url: str, title: str, summary: str = "") -> bool:
        """
        4-уровневая проверка на дубликат:
        1. URL (нормализованный)
        2. Хеш контента
        3. Похожесть заголовка (fuzzy)
        4. Пересечение ключевых сущностей
        """
        
        # === 1. URL ===
        norm_url = normalize_url(url)
        if norm_url in self.urls:
            logger.info(f"🚫 [URL] Дубликат: {title[:50]}...")
            return True

        # === 2. Хеш контента ===
        if summary:
            chash = get_content_hash(summary)
            if chash and chash in self.content_hashes:
                logger.info(f"🚫 [HASH] Дубликат: {title[:50]}...")
                return True

        # === 3. Похожесть заголовка ===
        title_len = len(title)
        for i, existing_title in enumerate(self.titles):
            if not existing_title:
                continue
            
            # Быстрый фильтр по длине
            if abs(len(existing_title) - title_len) > title_len * 0.6:
                continue
            
            sim = calculate_similarity(title, existing_title)
            if sim > config.similarity_threshold:
                logger.info(f"🚫 [TITLE {int(sim*100)}%] '{title[:35]}' ≈ '{existing_title[:35]}'")
                return True

        # === 4. Пересечение сущностей ===
        full_text = f"{title} {summary}".strip()
        new_entities = extract_key_entities(full_text)
        
        # Проверяем только если есть достаточно сущностей
        if len(new_entities) >= 2:
            for i, existing_entities in enumerate(self.topic_entities):
                if len(existing_entities) < 2:
                    continue
                
                common = new_entities & existing_entities
                
                # Считаем overlap относительно меньшего набора
                min_size = min(len(new_entities), len(existing_entities))
                overlap_ratio = len(common) / min_size if min_size > 0 else 0
                
                # Если совпадает 2+ сущности и overlap > порога
                if len(common) >= 2 and overlap_ratio >= config.entity_overlap_threshold:
                    existing_title = self.titles[i] if i < len(self.titles) else "?"
                    logger.info(f"🚫 [TOPIC] Совпадение: {common} | '{existing_title[:35]}'")
                    return True
        
        return False

    # 🆕 ПРОВЕРКА РАЗНООБРАЗИЯ С ПОСЛЕДНИМИ ПОСТАМИ
    def is_too_similar_to_recent(self, title: str, summary: str) -> bool:
        """
        Проверяет, не слишком ли похожа статья на последние N постов
        Более строгие пороги для свежих постов
        """
        if len(self.data) < 2:
            return False
        
        recent_posts = self.data[-config.recent_posts_check:]
        full_text = f"{title} {summary}".strip()
        new_entities = extract_key_entities(full_text)
        detected_topic = Topic.detect(full_text)
        
        for post in recent_posts:
            # Проверка 1: Похожесть заголовка (строже)
            post_title = post.get("title", "")
            if post_title:
                sim = calculate_similarity(title, post_title)
                if sim > config.recent_similarity_threshold:
                    logger.info(f"🔄 [RECENT] Слишком похоже на недавний пост: {post_title[:40]}")
                    return True
            
            # Проверка 2: Совпадение темы + сущностей
            post_topic = post.get("topic", "")
            post_entities = set(post.get("entities", []))
            
            if detected_topic == post_topic and post_entities:
                common = new_entities & post_entities
                if len(common) >= config.min_entity_distance:
                    logger.info(f"🔄 [RECENT] Та же тема '{detected_topic}' с похожими сущностями: {common}")
                    return True
        
        return False
    
    # 🆕 ПОЛУЧИТЬ СТАТИСТИКУ ПОСЛЕДНИХ ПОСТОВ
    def get_recent_topics_stats(self) -> dict:
        """Возвращает статистику по темам последних постов"""
        if len(self.data) < 3:
            return {}
        
        recent = self.data[-10:]
        stats = {}
        for post in recent:
            topic = post.get("topic", Topic.GENERAL)
            stats[topic] = stats.get(topic, 0) + 1
        
        return stats

    def add(self, url: str, title: str, summary: str = "", topic: str = Topic.GENERAL):
        """Добавляет статью в историю"""
        norm_url = normalize_url(url)
        
        # Проверка на случай повторного добавления
        if norm_url in self.urls:
            logger.debug(f"Уже есть в базе: {title[:40]}")
            return
        
        chash = get_content_hash(summary) if summary else ""
        full_text = f"{title} {summary}".strip()
        entities = extract_key_entities(full_text)
        
        # Обновляем кэши
        self.urls.add(norm_url)
        self.titles.append(title)
        self.topic_entities.append(entities)
        self.topics.append(topic)
        if chash:
            self.content_hashes.add(chash)
        
        # Добавляем запись
        self.data.append({
            "url": url,
            "norm_url": norm_url,
            "title": title[:200],
            "content_hash": chash,
            "entities": list(entities),
            "topic": topic,
            "ts": datetime.now(timezone.utc).isoformat() + "Z"
        })
        
        self._save()
        logger.info(f"💾 [{topic.upper()}] {title[:45]}... | Сущности: {entities if entities else 'нет'}")

    def cleanup(self, days: int = 30):
        """Удаляет записи старше N дней"""
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        old_count = len(self.data)
        
        self.data = [
            item for item in self.data
            if self._parse_ts(item.get("ts")) > cutoff
        ]
        
        removed = old_count - len(self.data)
        if removed > 0:
            self._rebuild_caches()
            self._save()
            logger.info(f"🧹 Очистка: удалено {removed} старых записей")

    def _parse_ts(self, ts: Optional[str]) -> float:
        if not ts:
            return 0
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except:
            return 0

    def __del__(self):
        self._release_lock()

# ====================== RSS LOADER ======================
async def fetch_feed(session: aiohttp.ClientSession, url: str, source: str, posted: PostedManager) -> List[Article]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                logger.warning(f"{source}: HTTP {resp.status}")
                return []
            text = await resp.text()
    except Exception as e:
        logger.warning(f"{source}: {e}")
        return []

    try:
        feed = feedparser.parse(text)
    except:
        return []

    articles = []
    for entry in feed.entries[:25]:
        link = entry.get("link", "").strip()
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1500]

        if not link or len(title) < 15:
            continue
        
        # Проверка дублей при загрузке
        if posted.is_duplicate(link, title, summary):
            continue

        published = datetime.now(timezone.utc)
        for df in ["published", "updated", "created"]:
            ds = entry.get(df)
            if ds:
                try:
                    parsed = feedparser._parse_date(ds)
                    if parsed:
                        published = datetime(*parsed[:6], tzinfo=timezone.utc)
                        break
                except:
                    pass

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
    
    # 🆕 Статистика последних тем
    recent_stats = posted.get_recent_topics_stats()
    logger.info(f"📊 Последние темы: {recent_stats}")
    
    for a in articles:
        text = f"{a.title} {a.summary}".lower()
        
        if any(p in text for p in BAD_PHRASES):
            continue
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue
        if not any(kw in text for kw in AI_KEYWORDS):
            continue
        if ai_relevance(text) < 0.4:
            continue
        
        # 🆕 ПРОВЕРКА НА ПОХОЖЕСТЬ С ПОСЛЕДНИМИ ПОСТАМИ
        if posted.is_too_similar_to_recent(a.title, a.summary):
            logger.debug(f"  Пропуск (слишком похоже на недавние): {a.title[:40]}")
            continue
        
        candidates.append(a)

    # Сортируем по дате (свежие первые)
    candidates.sort(key=lambda x: x.published, reverse=True)
    
    # 🆕 ПРИОРИТЕТ РАЗНЫМ ТЕМАМ
    # Если одна тема преобладает в последних постах, отдаём приоритет другим
    if recent_stats:
        dominant_topic = max(recent_stats, key=recent_stats.get)
        if recent_stats[dominant_topic] >= 3:  # Если 3+ поста подряд об одном
            logger.info(f"⚖️ Приоритет разнообразию (много '{dominant_topic}' в последних)")
            
            # Разделяем на доминантную тему и остальные
            other_topics = []
            same_topic = []
            
            for art in candidates:
                detected = Topic.detect(f"{art.title} {art.summary}")
                if detected == dominant_topic:
                    same_topic.append(art)
                else:
                    other_topics.append(art)
            
            # Сначала другие темы, потом доминантная
            candidates = other_topics + same_topic
    
    logger.info(f"🎯 После фильтров: {len(candidates)} статей")
    return candidates

# ====================== ГЕНЕРАТОР ПОСТОВ ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
]

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

    for attempt in range(3):
        try:
            await asyncio.sleep(0.5)
            
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=random.choice(GROQ_MODELS),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1100,
            )
            text = resp.choices[0].message.content.strip()

            # Проверка на SKIP
            if "SKIP" in text.upper()[:15]:
                logger.info("  ⏭️ LLM: SKIP")
                return None

            # Проверка длины
            if len(text) < config.min_post_length:
                logger.warning(f"  ⚠️ Короткий текст ({len(text)} симв.), повтор...")
                continue

            # Проверка на воду
            water = ["стоит отметить", "важно понимать", "интересно, что", 
                    "давайте разберёмся", "не секрет", "очевидно, что"]
            if any(w in text.lower() for w in water):
                logger.warning("  ⚠️ Обнаружена вода, повтор...")
                continue

            # Формируем финальный пост
            topic = Topic.detect(f"{article.title} {article.summary}")
            hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])
            
            cta = "\n\n🔥 — огонь  |  🗿 — ну такое  |  ⚡ — интересно"
            source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'
            
            final = f"{text}{cta}\n\n{hashtags}{source_link}"

            # Обрезка если превышает лимит
            if len(final) > config.caption_limit:
                excess = len(final) - config.caption_limit + 20
                text = text[:-excess]
                # Ищем последнюю точку/вопрос
                for p in ['. ', '! ', '? ']:
                    idx = text.rfind(p)
                    if idx > len(text) * 0.6:
                        text = text[:idx+1]
                        break
                final = f"{text}{cta}\n\n{hashtags}{source_link}"

            logger.info(f"  ✅ Готово: {len(text)} символов | Тема: {topic}")
            return final
            
        except Exception as e:
            logger.error(f"  ❌ Groq ошибка (попытка {attempt+1}): {e}")
            await asyncio.sleep(2)

    return None

# ====================== КАРТИНКИ ======================
async def generate_image(title: str) -> Optional[str]:
    logger.info("  🎨 Генерация картинки...")
    
    clean = re.sub(r'[^\w\s]', '', title)[:50]
    prompt = f"tech editorial illustration {clean} neon purple blue dark background 8k"
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=1024&height=1024&nologo=true&seed={random.randint(1,99999)}"
    
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
                    if len(data) < 10000:
                        continue
                    
                    fname = f"img_{random.randint(1000,9999)}.jpg"
                    with open(fname, "wb") as f:
                        f.write(data)
                    logger.info(f"  ✅ Картинка: {fname}")
                    return fname
        except:
            await asyncio.sleep(2)
    
    logger.warning("  ⚠️ Картинка не создана")
    return None

# ====================== ПУБЛИКАЦИЯ ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    img = await generate_image(article.title)
    
    try:
        if img and os.path.exists(img):
            await bot.send_photo(config.channel_id, FSInputFile(img), caption=text)
            os.remove(img)
        else:
            await bot.send_message(config.channel_id, text, disable_web_page_preview=False)
        
        # 🆕 Сохраняем с темой
        topic = Topic.detect(f"{article.title} {article.summary}")
        posted.add(article.link, article.title, article.summary, topic)
        
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
    logger.info("🚀 ЗАПУСК AI-POSTER v2.0")
    logger.info("=" * 50)
    
    posted = PostedManager(config.posted_file)
    posted.cleanup(config.retention_days)
    
    # Загружаем и фильтруем
    raw_articles = await load_all_feeds(posted)
    candidates = filter_articles(raw_articles, posted)
    
    if not candidates:
        logger.info("📭 Нет подходящих новостей")
        return

    # Пробуем опубликовать одну статью
    for article in candidates[:20]:  # 🆕 Увеличили до 20 попыток
        # Финальная проверка перед генерацией
        if posted.is_duplicate(article.link, article.title, article.summary):
            logger.debug(f"  Пропуск (дубль): {article.title[:40]}")
            continue
        
        # 🆕 Ещё одна проверка на разнообразие
        if posted.is_too_similar_to_recent(article.title, article.summary):
            logger.debug(f"  Пропуск (похоже на недавние): {article.title[:40]}")
            continue
        
        summary = await generate_summary(article)
        if not summary:
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






























































































































































































































































