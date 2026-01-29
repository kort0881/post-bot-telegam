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
from typing import List, Dict, Optional
from urllib.parse import urlparse, quote
from dataclasses import dataclass, field

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from groq import Groq

# Для блокировки файлов (Windows совместимость)
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
        
        # Порог похожести заголовков (0.75 = 75% сходства)
        self.similarity_threshold = 0.75

        missing = []
        for var, name in [(self.groq_api_key, "GROQ_API_KEY"),
                          (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                          (self.channel_id, "CHANNEL_ID")]:
            if not var:
                missing.append(name)
        if missing:
            raise SystemExit(f"❌ Отсутствуют переменные окружения: {', '.join(missing)}")

config = Config()

bot = Bot(token=config.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
groq_client = Groq(api_key=config.groq_api_key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
}

# ====================== RSS ======================
RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat AI"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review"),
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica AI"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED AI"),
]

# ====================== КЛЮЧЕВЫЕ СЛОВА ======================
AI_KEYWORDS = [
    "ai ", " ai", "artificial intelligence", "machine learning", "deep learning", "neural network",
    "llm", "large language model", "gpt", "chatgpt", "claude", "gemini", "grok", "llama",
    "mistral", "qwen", "deepseek", "midjourney", "dall-e", "stable diffusion", "sora", 
    "groq", "openai", "anthropic", "deepmind", "hugging face", "nvidia", "agi", 
    "inference", "rlhf", "transformer", "generative", "chatbot"
]

EXCLUDE_KEYWORDS = [
    "stock price", "ipo", "earnings call", "quarterly results", "revenue beat", "profit margin", 
    "dividend", "market cap", "wall street",
    "ps5", "xbox", "nintendo switch", "game review", "gameplay", "gaming pc",
    "netflix series", "movie review", "box office", "trailer", "premiere",
    "tesla stock", "ev sales", "model 3", "model y", "cybertruck",
    "bitcoin", "crypto", "blockchain", "nft", "ethereum",
    "election", "trump", "biden", "congress", "senate", "white house"
]

BAD_PHRASES = ["sponsored", "partner content", "advertisement", "black friday", "deal alert", "coupon"]

# ====================== DATACLASSES ======================
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
    GENERAL = "general"
    
    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #DALLE #StableDiffusion #генерация",
        ROBOTICS: "#роботы #Humanoid #робототехника",
        HARDWARE: "#NVIDIA #GPU #чипы #железо",
        GENERAL: "#AI #нейросети #искусственныйинтеллект"
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["gpt", "chatgpt", "claude", "gemini", "llama", "grok", "llm", "language model"]):
            return Topic.LLM
        if any(x in t for x in ["midjourney", "dall-e", "dalle", "stable diffusion", "flux", "image gen", "sora"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics", "optimus", "figure ai"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "h100", "h200", "blackwell", "gpu", "tensor core", "cuda"]):
            return Topic.HARDWARE
        return Topic.GENERAL

# ====================== HELPERS ======================
def normalize_url(url: str) -> str:
    """Агрессивная нормализация URL для удаления UTM-меток и дублей"""
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
    """Вычисляет коэффициент схожести двух строк (от 0.0 до 1.0)"""
    return difflib.SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

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
    """Генерирует короткий хеш контента"""
    if not text:
        return ""
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]

# ====================== POSTED MANAGER ======================
class PostedManager:
    def __init__(self, file="posted_articles.json"):
        self.file = file
        self.lock_file = file + ".lock"
        self.data = []
        self.urls = set()
        self.titles = []
        self.content_hashes = set()
        self._lock_fd = None
        
        self._acquire_lock()
        self._load()

    def _acquire_lock(self):
        """Блокировка для предотвращения одновременного запуска"""
        if not HAS_FCNTL:
            # Windows: простая проверка через файл
            if os.path.exists(self.lock_file):
                try:
                    # Проверяем, не устарел ли lock файл (> 10 минут)
                    age = datetime.now().timestamp() - os.path.getmtime(self.lock_file)
                    if age < 600:
                        raise SystemExit("⚠️ Другой экземпляр скрипта уже запущен")
                except OSError:
                    pass
            with open(self.lock_file, 'w') as f:
                f.write(str(os.getpid()))
            return
        
        # Linux/Mac: используем fcntl
        self._lock_fd = open(self.lock_file, 'w')
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
        except BlockingIOError:
            raise SystemExit("⚠️ Другой экземпляр скрипта уже запущен")

    def _release_lock(self):
        """Освобождение блокировки"""
        try:
            if HAS_FCNTL and self._lock_fd:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
        except Exception:
            pass

    def _load(self):
        if not os.path.exists(self.file):
            self._save()
            return
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            
            for item in self.data:
                url = item.get("url", "")
                if url:
                    self.urls.add(normalize_url(url))
                
                title = item.get("title", "")
                if title:
                    self.titles.append(title)
                
                content_hash = item.get("content_hash", "")
                if content_hash:
                    self.content_hashes.add(content_hash)
            
            logger.info(f"📚 Загружено {len(self.data)} опубликованных статей")
        except Exception as e:
            logger.error(f"Ошибка загрузки posted_articles.json: {e}")
            self.data = []

    def _save(self):
        """Атомарное сохранение данных"""
        try:
            # Создаём временный файл в той же директории
            dir_name = os.path.dirname(self.file) or '.'
            fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=dir_name)
            
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            
            # Атомарно заменяем файл
            shutil.move(tmp_path, self.file)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            # Удаляем временный файл если остался
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass

    def is_duplicate(self, url: str, title: str, summary: str = "") -> bool:
        """Комплексная проверка на дубликат"""
        
        # 1. Проверка по нормализованному URL
        norm_url = normalize_url(url)
        if norm_url in self.urls:
            logger.debug(f"🚫 Дубликат по URL: {url[:60]}")
            return True

        # 2. Проверка по хешу контента
        if summary:
            content_hash = get_content_hash(summary)
            if content_hash and content_hash in self.content_hashes:
                logger.info(f"🚫 Дубликат по контенту: {title[:50]}...")
                return True

        # 3. Проверка по похожести заголовка (все заголовки, с оптимизацией)
        title_len = len(title)
        for existing_title in self.titles:
            # Быстрый фильтр по длине (если длины сильно отличаются - пропускаем)
            if abs(len(existing_title) - title_len) > title_len * 0.5:
                continue
            
            similarity = calculate_similarity(title, existing_title)
            if similarity > config.similarity_threshold:
                logger.info(f"🚫 Дубликат по заголовку ({int(similarity*100)}%): '{title[:40]}...' ≈ '{existing_title[:40]}...'")
                return True
        
        return False

    def add(self, url: str, title: str, summary: str = ""):
        """Добавляет статью в историю публикаций"""
        norm_url = normalize_url(url)
        
        # Проверка на случай повторного добавления
        if norm_url in self.urls:
            return
        
        content_hash = get_content_hash(summary) if summary else ""
        
        # Обновляем кэши
        self.urls.add(norm_url)
        self.titles.append(title)
        if content_hash:
            self.content_hashes.add(content_hash)
        
        # Добавляем запись
        self.data.append({
            "url": url,
            "norm_url": norm_url,
            "title": title[:200],
            "content_hash": content_hash,
            "ts": datetime.now(timezone.utc).isoformat() + "Z"
        })
        
        self._save()
        logger.info(f"💾 Сохранено в историю: {title[:50]}...")

    def cleanup(self, days=30):
        """Удаляет записи старше N дней"""
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        old_count = len(self.data)
        
        self.data = [
            item for item in self.data
            if self._parse_ts(item.get("ts")) > cutoff
        ]
        
        removed = old_count - len(self.data)
        if removed > 0:
            # Перестраиваем кэши
            self.urls.clear()
            self.titles.clear()
            self.content_hashes.clear()
            
            for item in self.data:
                url = item.get("url", "")
                title = item.get("title", "")
                content_hash = item.get("content_hash", "")
                
                if url:
                    self.urls.add(normalize_url(url))
                if title:
                    self.titles.append(title)
                if content_hash:
                    self.content_hashes.add(content_hash)
            
            self._save()
            logger.info(f"🧹 Удалено {removed} старых записей из истории")

    def _parse_ts(self, ts_str: Optional[str]) -> float:
        if not ts_str:
            return 0
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except:
            return 0

    def __del__(self):
        self._release_lock()

# ====================== RSS LOADER ======================
async def fetch_feed(session: aiohttp.ClientSession, url: str, source: str, posted: PostedManager) -> List[Article]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"{source}: HTTP {resp.status}")
                return []
            text = await resp.text()
    except Exception as e:
        logger.warning(f"{source} недоступен: {e}")
        return []

    try:
        feed = feedparser.parse(text)
    except Exception as e:
        logger.error(f"{source}: ошибка парсинга RSS - {e}")
        return []

    articles = []
    for entry in feed.entries[:25]:
        link = entry.get("link", "").strip()
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1500]

        if not link or not title:
            continue
        
        if len(title) < 15:
            continue
            
        # ПРОВЕРКА НА ДУБЛИКАТЫ (URL + контент + заголовок)
        if posted.is_duplicate(link, title, summary):
            continue

        published = datetime.now(timezone.utc)
        for date_field in ["published", "updated", "created"]:
            date_str = entry.get(date_field)
            if date_str:
                try:
                    parsed = feedparser._parse_date(date_str)
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
    logger.info("🔄 Сканирование источников...")
    
    connector = aiohttp.TCPConnector(limit_per_host=5, limit=30)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        tasks = [fetch_feed(session, url, name, posted) for url, name in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    for res, (url, name) in zip(results, RSS_FEEDS):
        if isinstance(res, list) and res:
            all_articles.extend(res)
            logger.info(f"✅ {name}: {len(res)} новых статей")
        elif isinstance(res, Exception):
            logger.error(f"❌ {name}: {res}")

    logger.info(f"📊 Всего кандидатов: {len(all_articles)}")
    return all_articles

# ====================== FILTER ======================
def filter_articles(articles: List[Article]) -> List[Article]:
    candidates = []
    
    for a in articles:
        text = f"{a.title} {a.summary}".lower()

        if any(phrase in text for phrase in BAD_PHRASES):
            continue
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue
        if not any(kw in text for kw in AI_KEYWORDS):
            continue
        if ai_relevance(text) < 0.5:
            continue

        candidates.append(a)

    # Сортировка: сначала самые свежие
    candidates.sort(key=lambda x: x.published, reverse=True)
    logger.info(f"🎯 Прошло фильтры по ключевым словам: {len(candidates)} статей")
    return candidates

# ====================== SUMMARY + TRANSLATE ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]

async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация поста: {article.title[:60]}...")
    
    prompt = f"""Ты — редактор топового русскоязычного Telegram-канала про ИИ.

Оригинальная новость:
Заголовок: {article.title}
Текст: {article.summary[:2000]}

Задача:
Напиши пост на РУССКОМ языке.
Стиль: Живой, захватывающий, но без маркетинговой чепухи.
Структура:
1. Заголовок-хук (с эмодзи).
2. Суть новости (что произошло).
3. Почему это важно (контекст).
4. Заключение/вопрос.

Длина: до 900 символов.
Если новость мусорная или не про технологии/ИИ — ответь одним словом: SKIP

Текст поста:"""

    for attempt in range(3):
        try:
            await asyncio.sleep(1)
            
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=random.choice(GROQ_MODELS),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1100,
            )
            text = resp.choices[0].message.content.strip()

            if "SKIP" in text.upper()[:10]:
                logger.info("   ⚠️ LLM отклонила тему (SKIP)")
                return None

            topic = Topic.detect(f"{article.title} {article.summary}")
            hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])

            cta = "\n\n🔥 — огонь! | 🗿 — ну такое | ⚡ — прикольно"
            source = f'\n\n🔗 <a href="{article.link}">Оригинал</a>'
            final = text + cta + "\n\n" + hashtags + source

            if len(final) > config.caption_limit:
                overflow = len(final) - config.caption_limit + 50
                text = text[:-overflow]
                # Обрезаем аккуратно по точке
                for punct in ['.', '!', '?']:
                    last = text.rfind(punct)
                    if last > len(text) // 2:
                        text = text[:last + 1]
                        break
                final = text + cta + "\n\n" + hashtags + source

            return final
            
        except Exception as e:
            logger.error(f"   ❌ Groq ошибка (попытка {attempt+1}/3): {e}")
            await asyncio.sleep(3)

    return None

# ====================== IMAGE ======================
async def generate_image(title: str) -> Optional[str]:
    logger.info("   🎨 Генерация изображения...")
    
    clean_title = re.sub(r'[^\w\s]', '', title)[:60]
    prompt = f"editorial tech illustration, {clean_title}, isometric 3d, artificial intelligence theme, purple and blue neon lights, dark background, 8k"
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=1024&height=1024&nologo=true&enhance=true&seed={random.randint(1,999999)}"
    
    for attempt in range(3):
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(2)
                        continue
                    
                    content = await resp.read()
                    if len(content) < 5000:
                        continue
                    
                    fname = f"img_{int(datetime.now().timestamp())}_{random.randint(1000,9999)}.jpg"
                    with open(fname, "wb") as f:
                        f.write(content)
                    
                    logger.info(f"   ✅ Картинка готова: {fname}")
                    return fname
                    
        except Exception:
            await asyncio.sleep(3)
    
    logger.warning("   ⚠️ Картинку создать не удалось")
    return None

# ====================== POST ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    img = await generate_image(article.title)
    
    try:
        if img and os.path.exists(img):
            await bot.send_photo(config.channel_id, FSInputFile(img), caption=text)
            os.remove(img)
        else:
            await bot.send_message(config.channel_id, text, disable_web_page_preview=False)

        # Добавляем в базу опубликованных с контентом для хеширования
        posted.add(article.link, article.title, article.summary)
        logger.info(f"✅ УСПЕШНО ОПУБЛИКОВАНО: {article.title[:60]}...")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        if img and os.path.exists(img):
            try:
                os.remove(img)
            except:
                pass
        return False

# ====================== MAIN ======================
async def autopost():
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК СКРИПТА")
    logger.info("=" * 60)

    posted = PostedManager(config.posted_file)
    posted.cleanup(config.retention_days)

    raw = await load_all_feeds(posted)
    candidates = filter_articles(raw)

    if not candidates:
        logger.info("❌ Нет новостей для публикации")
        return

    # Берем ТОЛЬКО ОДНУ самую свежую и подходящую новость за запуск
    for article in candidates[:10]:
        # Двойная проверка перед генерацией
        if posted.is_duplicate(article.link, article.title, article.summary):
            continue

        summary = await generate_summary(article)
        if not summary:
            continue

        if await post_article(article, summary, posted):
            logger.info("\n🏁 Пост опубликован, скрипт завершает работу.")
            break 
        
        await asyncio.sleep(5)

async def main():
    posted = None
    try:
        await autopost()
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())





























































































































































































































































