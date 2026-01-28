import os
import json
import asyncio
import random
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urlparse
from dataclasses import dataclass, field

import aiohttp
import feedparser
import urllib.parse
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from groq import Groq

# ============ CONFIG ============

@dataclass
class Config:
    groq_api_key: str
    telegram_token: str
    channel_id: str
    retention_days: int = 30
    caption_limit: int = 1024
    posted_file: str = "posted_articles.json"
    
    @classmethod
    def from_env(cls) -> "Config":
        groq_key = os.getenv("GROQ_API_KEY")
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        channel = os.getenv("CHANNEL_ID")
        
        missing = []
        if not groq_key:
            missing.append("GROQ_API_KEY")
        if not tg_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not channel:
            missing.append("CHANNEL_ID")
        
        if missing:
            raise SystemExit(f"❌ CRITICAL: Отсутствуют переменные окружения: {', '.join(missing)}")
        
        return cls(
            groq_api_key=groq_key,
            telegram_token=tg_token,
            channel_id=channel,
        )

config = Config.from_env()

bot = Bot(
    token=config.telegram_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
groq_client = Groq(api_key=config.groq_api_key)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# ============ НОВЫЕ ЗАПАДНЫЕ ИСТОЧНИКИ ============

RSS_FEEDS = [
    # Лучшие профильные источники по AI
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat AI"),
    ("https://www.technologyreview.com/feed/topic/artificial-intelligence", "MIT Tech Review"),
    
    # Общие техно-гиганты (нужен строгий фильтр)
    ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ("http://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
    ("https://www.engadget.com/rss.xml", "Engadget"),
]

# ============ ОБНОВЛЕННЫЕ КЛЮЧЕВЫЕ СЛОВА (ENGLISH) ============
# Так как исходники теперь на английском, ищем английские слова

AI_KEYWORDS = [
    # Core terms
    "artificial intelligence", "neural network", "machine learning", "deep learning",
    "generative ai", "llm", "large language model", "foundation model",
    
    # Products & Models
    "chatgpt", "gpt-4", "gpt-5", "claude", "gemini", "copilot", "llama", 
    "mistral", "midjourney", "dall-e", "stable diffusion", "sora", "runway",
    "groq", "deepseek", "qwen",
    
    # Companies (Context check required usually, but these are strong signals)
    "openai", "anthropic", "deepmind", "hugging face", "nvidia", 
    
    # Tech terms
    "transformer", "inference", "fine-tuning", "dataset", "chatbot", 
    "computer vision", "nlp", "natural language processing",
    "agi", "autonomous agent", "rlhf", "prompt engineering",
    "text-to-video", "text-to-image",
]

EXCLUDE_KEYWORDS = [
    # Finance & Markets (English)
    "stock", "shares", "market cap", "earnings call", "quarterly results",
    "dividend", "ipo", "wall street", "investor", "revenue", "profit",
    "recession", "inflation", "central bank",
    
    # Games & Entertainment (Non-AI)
    "game review", "gameplay", "ps5", "xbox", "nintendo", "steam deck",
    "movie review", "box office", "trailer", "premiere", "netflix series",
    "actor", "actress", "director",
    
    # Cars (Non-AI)
    "electric car", "ev", "tesla model", "toyota", "bmw", "ford",
    "engine", "horsepower", "test drive", "mileage",
    
    # Politics & Crime
    "election", "vote", "senate", "congress", "white house",
    "arrest", "jail", "court", "lawsuit", "police", "crime",
    
    # Archaeology/History
    "archaeology", "ancient", "fossil", "dinosaur", "tomb", "excavation",
]

BAD_PHRASES = [
    "sponsored content", "partner content", "advertisement",
    "best deals", "black friday", "cyber monday",
    "buy now", "discount", "coupon",
]


# ============ ARTICLE DATACLASS ============

@dataclass
class Article:
    id: str
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=datetime.now)
    
    def get_full_text(self) -> str:
        return f"{self.title} {self.summary}"


# ============ TOPIC ENUM ============

class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    AI = "ai"
    
    HASHTAGS = {
        "llm": "#ChatGPT #LLM #нейросети",
        "image_gen": "#AI #генерация #нейросети",
        "robotics": "#роботы #AI #технологии",
        "hardware": "#железо #GPU #технологии",
        "ai": "#AI #нейросети #технологии",
    }
    
    @classmethod
    def detect(cls, text: str) -> str:
        text = text.lower()
        if any(kw in text for kw in ["gpt", "chatgpt", "claude", "llm", "gemini"]):
            return cls.LLM
        if any(kw in text for kw in ["midjourney", "dall-e", "stable diffusion", "image generation"]):
            return cls.IMAGE_GEN
        if any(kw in text for kw in ["robot", "robotics", "humanoid"]):
            return cls.ROBOTICS
        if any(kw in text for kw in ["nvidia", "gpu", "chip", "processor", "h100"]):
            return cls.HARDWARE
        return cls.AI
    
    @classmethod
    def get_hashtags(cls, topic: str) -> str:
        return cls.HASHTAGS.get(topic, cls.HASHTAGS[cls.AI])


# ============ HELPERS ============

def normalize_url(url: str) -> str:
    if not url: return ""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        domain = parsed.netloc.lower().replace("www.", "")
        return f"{domain}{path}"
    except: return url.split("?")[0].rstrip("/")

def extract_article_id(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.md5(normalized.encode()).hexdigest()[:16]

def clean_text(text: str) -> str:
    if not text: return ""
    # Удаляем HTML теги для чистоты проверки ключевых слов
    clean = re.sub(r'<[^>]+>', '', text)
    return " ".join(clean.replace("\n", " ").split())

def has_exact_keyword(text: str, keywords: List[str]) -> Optional[str]:
    text_lower = text.lower()
    words = set(re.findall(r'\b[\w-]+\b', text_lower))
    for kw in keywords:
        kw_lower = kw.lower()
        if " " in kw_lower:
            if kw_lower in text_lower: return kw
        elif kw_lower in words: return kw
    return None

def has_ai_keyword(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in AI_KEYWORDS)

def build_final_post(core_text: str, hashtags: str, link: str, max_total: int = 1024) -> str:
    cta_line = "\n\n🔥 — огонь! | 🗿 — ну такое | ⚡ — прикольно"
    source_line = f'\n🔗 <a href="{link}">Источник</a>'
    hashtag_line = f"\n\n{hashtags}"
    
    reserved = len(cta_line) + len(hashtag_line) + len(source_line) + 20
    max_core = max_total - reserved
    
    if len(core_text) > max_core:
        core_text = core_text[:max_core]
        last_punct = max(core_text.rfind('.'), core_text.rfind('!'), core_text.rfind('?'))
        if last_punct > max_core // 2:
            core_text = core_text[:last_punct + 1]
    
    return core_text + cta_line + hashtag_line + source_line


# ============ POSTED MANAGER ============

class PostedManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.posted_ids: set = set()
        self.posted_urls: set = set()
        self.data: List[Dict] = []
        self._load()
    
    def _load(self):
        if not os.path.exists(self.filepath): self._save(); return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            for item in self.data:
                if "id" in item:
                    url = item["id"]
                    self.posted_urls.add(normalize_url(url))
                    self.posted_ids.add(extract_article_id(url))
        except: self.data = []
    
    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except: pass
    
    def is_posted(self, url: str) -> bool:
        return extract_article_id(url) in self.posted_ids or normalize_url(url) in self.posted_urls
    
    def add(self, url: str, title: str = ""):
        self.posted_ids.add(extract_article_id(url))
        self.posted_urls.add(normalize_url(url))
        self.data.append({"id": url, "title": title[:100], "timestamp": datetime.now().timestamp()})
        self._save()
    
    def cleanup(self, days: int = 30):
        cutoff = datetime.now().timestamp() - (days * 86400)
        self.data = [i for i in self.data if i.get("timestamp", 0) > cutoff]
        self._save()
    
    def count(self) -> int: return len(self.data)


# ============ LOGIC ============

async def load_rss_async(session: aiohttp.ClientSession, url: str, source: str, posted_manager: PostedManager) -> List[Article]:
    articles = []
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200: return []
            content = await resp.text()
            feed = feedparser.parse(content)
            if feed.bozo and not feed.entries: return []
    except: return []
    
    for entry in feed.entries[:20]:
        link = entry.get("link", "")
        if not link or posted_manager.is_posted(link): continue
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1000]
        if not title: continue
        
        articles.append(Article(id=link, title=title, summary=summary, link=link, source=source))
    return articles

async def load_all_feeds(posted_manager: PostedManager) -> List[Article]:
    print("\n🔄 Scanning Western Tech Sources...")
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = [load_rss_async(session, url, name, posted_manager) for url, name in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    articles = []
    for i, result in enumerate(results):
        if isinstance(result, list):
            articles.extend(result)
            if result: print(f"✅ {RSS_FEEDS[i][1]}: found {len(result)}")
            
    print(f"📊 Total raw articles: {len(articles)}")
    return articles

def filter_articles(articles: List[Article]) -> List[Article]:
    valid = []
    for article in articles:
        text = article.get_full_text()
        
        # 1. Filter Exclusions (English)
        if has_exact_keyword(text, EXCLUDE_KEYWORDS): continue
        if any(p in text.lower() for p in BAD_PHRASES): continue
        
        # 2. Require AI Keywords (English)
        if not has_ai_keyword(text): continue
        
        valid.append(article)
    
    valid.sort(key=lambda x: x.published, reverse=True)
    print(f"🎯 Candidates after filtering: {len(valid)}")
    return valid

async def generate_summary(article: Article) -> Optional[str]:
    print(f"   📝 Translating & Processing: {article.title[:50]}...")
    
    # === ГЛАВНОЕ ИЗМЕНЕНИЕ: ПРОМПТ НА ПЕРЕВОД ===
    prompt = f"""
You are an expert editor for a Russian Telegram channel about AI Technology.
Your task is to READ the English news and WRITE a post in RUSSIAN.

SOURCE (English):
Title: {article.title}
Content: {article.summary}

INSTRUCTIONS:
1.  **TRANSLATE** the core meaning to Russian. Do not just translate word-for-word, adapt it for Russian readers.
2.  **FORMAT**:
    - Start with a hook (e.g., "Всем привет! 👋" or "Новости AI ⚡").
    - Explain WHAT happened and WHY it matters.
    - Keep it simple, friendly, and informative.
3.  **LENGTH**: 600-800 characters (Russian text).
4.  **STRICT RULE**: If the news is NOT about Artificial Intelligence, Machine Learning, or Neural Networks (e.g. it is about general gadgets, crypto, or politics) -> OUTPUT ONLY THE WORD: "SKIP".

Output language must be **RUSSIAN**.
"""
    
    try:
        response = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=1000,
            )
        )
        content = response.choices[0].message.content.strip()
        
        if "SKIP" in content.upper():
            print("   ⚠️ Skipped by AI (Off-topic)")
            return None
        
        topic = Topic.detect(f"{article.title} {article.summary}")
        hashtags = Topic.get_hashtags(topic)
        
        return build_final_post(content, hashtags, article.link, config.caption_limit)
    except Exception as e:
        print(f"   ❌ Generation error: {e}")
        return None

async def generate_image(title: str) -> Optional[str]:
    # Для Pollinations английский заголовок (как сейчас) подходит идеально!
    try:
        clean_title = re.sub(r'[^\w\s]', '', title)[:50]
        prompt = f"futuristic AI technology illustration, {clean_title}, minimalist, 4k, neon light, dark background"
        url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?width=1024&height=1024&nologo=true&seed={random.randint(0,10000)}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    fname = f"temp_{random.randint(0,999)}.jpg"
                    with open(fname, "wb") as f: f.write(await resp.read())
                    return fname
    except: pass
    return None

async def post_to_channel(article: Article, text: str, posted_manager: PostedManager) -> bool:
    img_path = await generate_image(article.title)
    try:
        if img_path:
            await bot.send_photo(config.channel_id, photo=FSInputFile(img_path), caption=text)
        else:
            await bot.send_message(config.channel_id, text=text, disable_web_page_preview=False)
        
        posted_manager.add(article.link, article.title)
        print(f"✅ PUBLISHED: {article.title[:40]}...")
        return True
    except Exception as e:
        print(f"❌ Telegram Error: {e}")
        return False
    finally:
        if img_path and os.path.exists(img_path): os.remove(img_path)

# ============ MAIN ============

async def autopost():
    print(f"\n{'='*40}\n🚀 STARTING (Western Sources Mode)\n{'='*40}")
    posted_manager = PostedManager(config.posted_file)
    posted_manager.cleanup(config.retention_days)
    
    raw = await load_all_feeds(posted_manager)
    candidates = filter_articles(raw)
    
    if not candidates: print("❌ No news found."); return
    
    for article in candidates:
        text = await generate_summary(article)
        if not text: continue
        
        if await post_to_channel(article, text, posted_manager):
            break 
        await asyncio.sleep(2)
        
    print("\n🏁 DONE")

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())


























































































































































































































































