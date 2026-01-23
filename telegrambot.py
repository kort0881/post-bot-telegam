import os
import json
import asyncio
import random
import re
import time
import hashlib
import html
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
import feedparser
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from openai import OpenAI

# ============ CONFIG ============

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not all([OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID]):
    print("⚠️ WARNING: Keys not found!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

CACHE_DIR = "cache_tech"
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "state_ai_full.json")

RETENTION_DAYS = 60
MAX_ARTICLE_AGE_DAYS = 2
TELEGRAM_CAPTION_LIMIT = 1024

# ============ ИСТОЧНИКИ ============

RSS_SOURCES = [
    {"name": "Habr AI", "url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "category": "ai"},
    {"name": "NeuroHive", "url": "https://neurohive.io/ru/feed/", "category": "ai"},
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "category": "ai"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "ai"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/artificial-intelligence/index.xml", "category": "ai"},
    # Общие техно-сайты (будем жестко фильтровать)
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "category": "tech_ru"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech_ru"},
]

CATEGORY_ROTATION = ["ai", "ai", "tech_ru", "ai"]

# ============ ФИЛЬТРЫ (СТОП-СЛОВА) ============

# Если эти слова есть в заголовке или тексте — СКИПАЕМ
BLOCK_KEYWORDS = [
    # Финансы и скука
    "акции", "дивиденд", "квартальный отчет", "отчетность", "прибыль", 
    "выручка", "цб рф", "курс валют", "инфляци", "сбер", "газпром", 
    "назначен", "уволен", "директор",
    
    # Спорт и развлечения (не по теме)
    "футбол", "хоккей", "матч", "спорт", "фильм", "сериал", "кино", 
    "актер", "звезд", "шоу", "евровидение",
    
    # Реклама и продажи
    "скидк", "распродаж", "выгодн", "покупай", "цена", "цены", 
    "магазин", "маркетплейс", "wildberries", "ozon",
    
    # Политика и криминал
    "выборы", "политик", "депутат", "закон", "суд", "арест", 
    "убийств", "мвд", "фсб", "теракт",
    
    # Не профильное IT (безопасность оставим для второго бота)
    "ddos", "фишинг", "хакер", "взлом"
]

# Обязательные слова для категории "tech_ru"
AI_KEYWORDS = [
    "нейросет", "ии", "ai", "gpt", "llm", "diffusion", "genai", 
    "nvidia", "робот", "deepmind", "openai", "sam altman", "маск",
    "алгоритм", "machine learning", "интеллект"
]

# ============ УТИЛИТЫ ============

class State:
    def __init__(self):
        self.data = {"content_hashes": {}, "url_hashes": {}, "category_index": 0}
        self._load()
    
    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f: self.data.update(json.load(f))
            except: pass
    
    def save(self):
        try:
            with open(STATE_FILE, "w") as f: json.dump(self.data, f, indent=2)
        except: pass
    
    def calculate_hash(self, text: str) -> str:
        return hashlib.sha256(text.strip().lower().encode()).hexdigest()

    def is_duplicate(self, title: str, link: str) -> bool:
        if self.calculate_hash(title) in self.data["content_hashes"]: return True
        if self.calculate_hash(link) in self.data["url_hashes"]: return True
        return False
    
    def mark_posted(self, title: str, link: str):
        ts = datetime.now().timestamp()
        self.data["content_hashes"][self.calculate_hash(title)] = ts
        self.data["url_hashes"][self.calculate_hash(link)] = ts
        self.save()

    def get_next_category(self) -> str:
        idx = self.data.get("category_index", 0)
        cat = CATEGORY_ROTATION[idx % len(CATEGORY_ROTATION)]
        self.data["category_index"] = (idx + 1) % len(CATEGORY_ROTATION)
        self.save()
        return cat

    def cleanup_old(self):
        cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
        self.data["content_hashes"] = {k: v for k, v in self.data["content_hashes"].items() if v > cutoff}
        self.data["url_hashes"] = {k: v for k, v in self.data["url_hashes"].items() if v > cutoff}
        self.save()

state = State()

def clean_text(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    return " ".join(text.split())

def force_complete_sentence(text: str) -> str:
    if not text: return ""
    if text[-1] in ".!?": return text
    cut_pos = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
    if cut_pos < len(text) * 0.7:
        return text.strip() + "."
    return text[:cut_pos+1]

def build_final_post(text: str, link: str) -> str:
    text = html.escape(text)
    text = force_complete_sentence(text)
    
    cta = "\n\n🔥 — круто | 👾 — жутко"
    source = f'\n🔗 <a href="{link}">Источник</a>'
    tags = "\n\n#AI #Tech #Нейросети #Будущее"
    
    full_len = len(text) + len(cta) + len(source) + len(tags)
    
    if full_len > TELEGRAM_CAPTION_LIMIT:
        available = TELEGRAM_CAPTION_LIMIT - len(cta) - len(source) - len(tags) - 50
        text = text[:available] + "..."
        
    return text + cta + tags + source

# ============ ПАРСИНГ ============

def fetch_full_article(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']): tag.decompose()
        content = soup.find('div', class_=re.compile(r'article|post|content'))
        if content: return clean_text(content.get_text())[:3000]
    except: pass
    return None

def load_rss(source: Dict) -> List[Dict]:
    articles = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        feed = feedparser.parse(resp.content)
    except: return []
    
    now = datetime.now()
    
    for entry in feed.entries[:15]:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
        
        if not title or not link: continue
        if state.is_duplicate(title, link): continue
        
        full_text_check = (title + " " + summary).lower()

        # 1. Проверка на СТОП-СЛОВА (Футбол, Акции и т.д.)
        if any(bad in full_text_check for bad in BLOCK_KEYWORDS): 
            continue

        # 2. Если источник общий (Tech_Ru), ищем обязательные AI слова
        if source["category"] == "tech_ru":
            if not any(good in full_text_check for good in AI_KEYWORDS):
                continue

        pub_date = now
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try: pub_date = datetime(*entry.published_parsed[:6])
            except: pass
            
        if now - pub_date > timedelta(days=MAX_ARTICLE_AGE_DAYS): continue
        
        articles.append({
            "title": title, "summary": summary, "link": link, 
            "source": source["name"], "published": pub_date
        })
    return articles

# ============ AI ГЕНЕРАЦИЯ (ХАЙП СТИЛЬ) ============

async def generate_post(article: Dict) -> Optional[str]:
    full_text = fetch_full_article(article["link"])
    content = full_text if full_text else article["summary"]
    
    prompt = f"""
    Ты — популярный техно-блогер.
    Твоя задача: Написать пост на основе новости, который вызовет ВОСТОРГ.
    
    Новость: {article['title']}
    Текст: {content[:2000]}

    ПРИМЕР СТИЛЯ:
    "🚀 Внимание, гики! SpaceX на пороге крупнейшего IPO... это просто космос! 🔥 Такое событие станет катализатором... Оставайтесь с нами!"

    ТРЕБОВАНИЯ:
    1. Вступление: Яркое, с эмодзи (🚀, ⚡️), обращение к гикам.
    2. Суть: Сильные глаголы ("взорвал", "потряс"). Без воды.
    3. Финал: Вдохновляющий вывод + призыв.
    4. Объем: до 700 знаков.
    5. Язык: Русский.
    """
    
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7 
        )
        raw_text = resp.choices[0].message.content.strip().replace("**", "")
        return build_final_post(raw_text, article["link"])
    except Exception as e:
        print(f"❌ OpenAI Error: {e}")
        return None

# ============ КАРТИНКИ ============

def generate_image(title: str) -> Optional[str]:
    clean_title = re.sub(r'[^a-zA-Z0-9]', ' ', title)[:50]
    prompt = f"futuristic ai concept art {clean_title} cyberpunk neon glowing 8k render"
    
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={random.randint(0, 999999)}"
    
    print(f"   🎨 Генерирую картинку...")
    try:
        resp = requests.get(url, timeout=30)
        
        if resp.status_code == 200 and len(resp.content) > 10000:
            fname = f"img_{int(time.time())}.jpg"
            with open(fname, "wb") as f: f.write(resp.content)
            return fname
    except Exception as e:
        print(f"   ❌ Ошибка загрузки картинки: {e}")
    
    return None

def cleanup_image(path):
    if path and os.path.exists(path):
        try: os.remove(path)
        except: pass

# ============ MAIN ============

async def autopost():
    state.cleanup_old()
    print("\n🚀 [AI Hype Bot] Старт...")
    
    all_articles = []
    target_cat = state.get_next_category()
    print(f"🎯 Категория: {target_cat}")
    
    sources = [s for s in RSS_SOURCES if s["category"] == target_cat]
    
    for source in sources:
        print(f"   📡 {source['name']}...")
        all_articles.extend(load_rss(source))
        
    if not all_articles:
        print("❌ Пусто в категории, ищу везде...")
        for source in RSS_SOURCES:
            all_articles.extend(load_rss(source))

    if not all_articles:
        print("💤 Нет подходящих новостей.")
        return

    all_articles.sort(key=lambda x: x["published"], reverse=True)
    
    for article in all_articles[:5]:
        print(f"\n📝 Обработка: {article['title']}")
        
        post_text = await generate_post(article)
        if not post_text: continue
        
        img_path = generate_image(article["title"])
        
        try:
            if img_path:
                await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img_path), caption=post_text)
            else:
                await bot.send_message(CHANNEL_ID, text=post_text, disable_web_page_preview=False)
            
            state.mark_posted(article["title"], article["link"])
            print("✅ УСПЕХ!")
            cleanup_image(img_path)
            return 
            
        except Exception as e:
            print(f"❌ Ошибка отправки TG: {e}")
            cleanup_image(img_path)

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())


















































































































































































































































