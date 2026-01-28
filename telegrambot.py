import os
import json
import asyncio
import random
import re
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.parse import urlparse, quote

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
        self.retention_days = int(os.getenv("RETENTION_DAYS", "30"))
        self.caption_limit = 1024
        self.posted_file = "posted_articles.json"

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
from dataclasses import dataclass, field

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
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        domain = parsed.netloc.lower().replace("www.", "")
        return f"{domain}{path}".split("?")[0].split("#")[0]
    except:
        return url.split("?")[0].split("#")[0]

def article_id(url: str) -> str:
    return hashlib.md5(normalize_url(url).encode()).hexdigest()[:16]

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

# ====================== POSTED MANAGER ======================
class PostedManager:
    def __init__(self, file="posted_articles.json"):
        self.file = file
        self.data = []
        self.ids = set()
        self.urls = set()
        self._load()

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
                    self.ids.add(article_id(url))
                    self.urls.add(normalize_url(url))
            
            logger.info(f"Загружено {len(self.data)} опубликованных статей")
        except Exception as e:
            logger.error(f"Ошибка загрузки posted_articles.json: {e}")
            self.data = []

    def _save(self):
        try:
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")

    def is_posted(self, url: str) -> bool:
        return article_id(url) in self.ids or normalize_url(url) in self.urls

    def add(self, url: str, title: str):
        aid = article_id(url)
        nurl = normalize_url(url)
        
        if aid in self.ids or nurl in self.urls:
            return
        
        self.ids.add(aid)
        self.urls.add(nurl)
        self.data.append({
            "url": url,
            "title": title[:100],
            "ts": datetime.now(timezone.utc).isoformat() + "Z"
        })
        self._save()

    def cleanup(self, days=30):
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        old_count = len(self.data)
        
        self.data = [
            item for item in self.data
            if self._parse_ts(item.get("ts")) > cutoff
        ]
        
        removed = old_count - len(self.data)
        if removed > 0:
            self.ids.clear()
            self.urls.clear()
            for item in self.data:
                url = item.get("url", "")
                if url:
                    self.ids.add(article_id(url))
                    self.urls.add(normalize_url(url))
            
            self._save()
            logger.info(f"Удалено {removed} старых записей")
    
    def _parse_ts(self, ts_str: Optional[str]) -> float:
        if not ts_str:
            return 0
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except:
            return 0

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
        if not link or posted.is_posted(link):
            continue

        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1500]

        if not title or len(title) < 15:
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
    logger.info("🔄 Сканирование западных источников...")
    
    connector = aiohttp.TCPConnector(limit_per_host=5, limit=30)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        tasks = [fetch_feed(session, url, name, posted) for url, name in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    for res, (url, name) in zip(results, RSS_FEEDS):
        if isinstance(res, list) and res:
            all_articles.extend(res)
            logger.info(f"✅ {name}: {len(res)} статей")
        elif isinstance(res, Exception):
            logger.error(f"❌ {name}: {res}")

    logger.info(f"📊 Всего собрано: {len(all_articles)}")
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

    candidates.sort(key=lambda x: x.published, reverse=True)
    logger.info(f"🎯 Прошло фильтры: {len(candidates)} статей")
    return candidates

# ====================== SUMMARY + TRANSLATE ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]

async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Обработка: {article.title[:60]}...")
    
    prompt = f"""Ты — редактор топового русскоязычного Telegram-канала про искусственный интеллект (50к+ подписчиков).

Оригинальная новость (English):
Заголовок: {article.title}
Описание: {article.summary[:2000]}

Напиши пост на РУССКОМ языке в живом, эмоциональном стиле:
- Начни с яркого хука (вопрос, восклицание, эмодзи)
- Объясни простыми словами, ЧТО произошло и ПОЧЕМУ это важно
- Добавь 1-2 своих комментария в духе «это реально прорыв», «конкуренты в шоке», «ждали годами»
- Длина: 600-850 символов
- Закончи вопросом или призывом к обсуждению

ВАЖНО: Если новость НЕ про ИИ, нейросети или ML (например, про финансы, политику, игры) — ответь ТОЛЬКО: SKIP

Пиши по-русски!"""

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

            if "SKIP" in text.upper()[:50]:
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

# ====================== IMAGE (С ЛОГАМИ!) ======================
async def generate_image(title: str) -> Optional[str]:
    logger.info("   🎨 Начинаю генерацию изображения...")
    
    clean_title = re.sub(r'[^\w\s]', '', title)[:60]
    prompt = f"minimalist futuristic AI technology illustration, {clean_title}, dark background, neon glow, cyberpunk aesthetic, 4k quality"
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?width=1024&height=1024&nologo=true&enhance=true&seed={random.randint(1,999999)}"
    
    logger.info(f"   📡 URL: {url[:100]}...")

    for attempt in range(3):
        try:
            logger.info(f"   🔄 Попытка {attempt + 1}/3...")
            
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    logger.info(f"   📊 HTTP Status: {resp.status}")
                    
                    if resp.status != 200:
                        logger.warning(f"   ⚠️ Плохой статус: {resp.status}")
                        await asyncio.sleep(3)
                        continue
                    
                    content = await resp.read()
                    size = len(content)
                    logger.info(f"   💾 Размер: {size} байт")
                    
                    if size < 10000:
                        logger.warning(f"   ⚠️ Слишком маленький файл: {size} байт")
                        await asyncio.sleep(3)
                        continue
                    
                    fname = f"img_{int(datetime.now().timestamp())}_{random.randint(1000,9999)}.jpg"
                    with open(fname, "wb") as f:
                        f.write(content)
                    
                    logger.info(f"   ✅ Изображение сохранено: {fname}")
                    return fname
                    
        except asyncio.TimeoutError:
            logger.warning(f"   ⏱️ Timeout на попытке {attempt + 1}")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"   ❌ Ошибка генерации: {type(e).__name__}: {e}")
            await asyncio.sleep(3)
    
    logger.warning("   ⚠️ Не удалось сгенерировать изображение после 3 попыток")
    return None

# ====================== POST ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    img = await generate_image(article.title)
    
    try:
        if img and os.path.exists(img):
            logger.info(f"   📤 Отправка с изображением...")
            await bot.send_photo(config.channel_id, FSInputFile(img), caption=text)
            os.remove(img)
            logger.info(f"   🗑️ Временный файл удалён")
        else:
            logger.info(f"   📤 Отправка БЕЗ изображения (текст only)")
            await bot.send_message(config.channel_id, text, disable_web_page_preview=False)

        posted.add(article.link, article.title)
        logger.info(f"✅ ОПУБЛИКОВАНО: {article.title[:60]}...")
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
    logger.info("🚀 ЗАПУСК АВТОПОСТЕРА (Western AI News → RU)")
    logger.info("=" * 60)

    posted = PostedManager(config.posted_file)
    posted.cleanup(config.retention_days)

    raw = await load_all_feeds(posted)
    if not raw:
        logger.info("❌ Новых статей не найдено")
        return

    candidates = filter_articles(raw)
    if not candidates:
        logger.info("❌ Нет подходящих новостей после фильтрации")
        return

    for i, article in enumerate(candidates[:10], 1):
        logger.info(f"\n[{i}/{min(10, len(candidates))}] Попытка: {article.source}")
        
        summary = await generate_summary(article)
        if not summary:
            logger.info("   ⏩ Пропуск, пробуем следующую...")
            continue

        if await post_article(article, summary, posted):
            logger.info("\n✨ Пост успешно опубликован! Завершаю работу.")
            break
        
        await asyncio.sleep(3)
    else:
        logger.warning("\n⚠️ Не удалось опубликовать ни одной статьи из топ-10")

    logger.info("=" * 60)
    logger.info("🏁 Работа завершена")
    logger.info("=" * 60)

async def main():
    try:
        await autopost()
    except KeyboardInterrupt:
        logger.info("\n⛔ Остановлено пользователем")
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())





























































































































































































































































