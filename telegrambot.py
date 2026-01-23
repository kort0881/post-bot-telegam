import os
import json
import asyncio
import random
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
import feedparser
import urllib.parse
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from openai import OpenAI

# ============ COPILOT SDK SETUP ============
try:
    from github_copilot_sdk import CopilotClient
    COPILOT_SDK_AVAILABLE = True
except ImportError:
    COPILOT_SDK_AVAILABLE = False
    print("⚠️ SDK не найден, работаем через OpenAI")

# ============ CONFIG ============

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
# Включаем SDK только если он доступен и разрешен
USE_COPILOT_SDK = os.getenv("USE_COPILOT_SDK", "false").lower() == "true" and COPILOT_SDK_AVAILABLE

if not all([OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID]):
    raise ValueError("❌ Не все ENV переменные установлены!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Инициализация Copilot Client
copilot_client = None
if USE_COPILOT_SDK:
    try:
        copilot_client = CopilotClient()
        print("🤖 Copilot Client инициализирован")
    except Exception as e:
        print(f"❌ Ошибка инициализации Copilot: {e}")
        USE_COPILOT_SDK = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

CACHE_DIR = os.getenv("CACHE_DIR", "cache_tech")
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "state_ai_v3.json") # Версия 3 чтобы сбросить старый кэш с security

RETENTION_DAYS = 60
MAX_ARTICLE_AGE_DAYS = 2
TELEGRAM_CAPTION_LIMIT = 1024

# ============ ИСТОЧНИКИ (ТОЛЬКО AI/TECH) ============

RSS_SOURCES = [
    # Русскоязычные AI
    {"name": "Habr AI", "url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "category": "ai"},
    {"name": "Habr ML", "url": "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "category": "ai"},
    {"name": "NeuroHive", "url": "https://neurohive.io/ru/feed/", "category": "ai"},
    
    # Англоязычные AI (переведем)
    {"name": "OpenAI Blog", "url": "https://openai.com/blog/rss.xml", "category": "ai"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "ai"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/artificial-intelligence/index.xml", "category": "ai"},
    
    # Общие Техно (фильтруем)
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "category": "tech_ru"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech_ru"},
    
    # Робототехника
    {"name": "Habr Robotics", "url": "https://habr.com/ru/rss/hub/robotics/all/?fl=ru", "category": "robotics"},
]

# УБРАЛИ SECURITY ИЗ РОТАЦИИ
CATEGORY_ROTATION = ["ai", "ai", "tech_ru", "ai", "robotics", "ai", "tech_ru"]

# ============ СТИЛИ ПОСТОВ ============

POST_STYLES = [
    {
        "name": "гик",
        "intro": "Новости будущего! 🤖",
        "tone": "Энергичный, увлекательный",
        "emojis": "⚡️🧠🚀"
    },
    {
        "name": "аналитик",
        "intro": "Важное из мира AI.",
        "tone": "Спокойный, экспертный",
        "emojis": "📊💡📱"
    }
]

# ============ ФИЛЬТРЫ ============

# Если в тексте есть эти слова - ЭТО ТОЧНО НЕ ДЛЯ ЭТОГО КАНАЛА
BLOCK_KEYWORDS = [
    "ddos", "хакеры", "взлом", "кибермошен", "фишинг", "infowatch", 
    "роскомнадзор", "нкцки", "вредонос", "уязвимость", "cve-",
    "акции", "дивиденды", "цб рф", "инфляция"
]

AI_KEYWORDS = [
    "нейросет", "ии", "ai", "gpt", "llm", "diffusion", "genai", 
    "nvidia", "робот", "automata", "deepmind", "openai", "sam altman",
    "mask", "генерация", "интеллект", "алгоритм"
]

def is_blocked(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    for kw in BLOCK_KEYWORDS:
        if kw in text: return True
    return False

# ============ STATE MANAGEMENT ============

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
    
    def cleanup_old(self):
        cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
        self.data["content_hashes"] = {k: v for k, v in self.data["content_hashes"].items() if v > cutoff}
        self.data["url_hashes"] = {k: v for k, v in self.data["url_hashes"].items() if v > cutoff}
        self.save()
    
    def get_next_category(self) -> str:
        idx = self.data.get("category_index", 0)
        cat = CATEGORY_ROTATION[idx % len(CATEGORY_ROTATION)]
        self.data["category_index"] = (idx + 1) % len(CATEGORY_ROTATION)
        self.save()
        return cat

state = State()

# ============ TEXT TOOLS ============

def clean_text(text: str) -> str:
    return re.sub(r'<[^>]+>', ' ', text).strip() if text else ""

def force_complete_sentence(text: str) -> str:
    """Умная обрезка текста"""
    if not text: return ""
    # Если заканчивается на точку/воскл/вопрос - ок
    if text[-1] in ".!?": return text
    
    # Ищем последнюю точку
    last_p = text.rfind('.')
    last_e = text.rfind('!')
    last_q = text.rfind('?')
    
    cut_pos = max(last_p, last_e, last_q)
    
    # Если нашли знак препинания в конце
    if cut_pos > len(text) * 0.7:
        return text[:cut_pos+1]
        
    return text.strip() + "."

def build_final_post(text: str, link: str) -> str:
    # 1. Сначала гарантируем целостность предложений
    text = force_complete_sentence(text)
    
    cta = "\n\n🔥 — круто | 👾 — жутко"
    source = f'\n🔗 <a href="{link}">Читать полностью</a>'
    tags = "\n\n#AI #Tech #Будущее #Нейросети"
    
    # 2. Проверяем лимит
    full_len = len(text) + len(cta) + len(source) + len(tags)
    
    if full_len > TELEGRAM_CAPTION_LIMIT:
        # Если не влезает, обрезаем жестче, но снова ищем точку
        available = TELEGRAM_CAPTION_LIMIT - len(cta) - len(source) - len(tags) - 50
        text = text[:available]
        text = force_complete_sentence(text)
        
    return text + cta + tags + source

# ============ PARSING ============

def fetch_full_article(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']): tag.decompose()
        
        # Поиск основного контента
        content = soup.find('div', class_=re.compile(r'article|post-content|entry-content'))
        if content: return content.get_text(separator='\n', strip=True)[:3000]
    except: pass
    return None

def load_rss(source: Dict) -> List[Dict]:
    articles = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        feed = feedparser.parse(resp.content)
    except: return []
    
    now = datetime.now()
    for entry in feed.entries[:20]:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
        
        if not title or not link: continue
        if state.is_duplicate(title, link): continue
        if is_blocked(title, summary): continue # Блокируем Security темы
        
        # Для Tech_Ru берем только если есть ключевые слова AI
        if source["category"] == "tech_ru":
            full_check = f"{title} {summary}".lower()
            if not any(k in full_check for k in AI_KEYWORDS):
                continue

        pub_date = now
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try: pub_date = datetime(*entry.published_parsed[:6])
            except: pass
            
        if now - pub_date > timedelta(days=MAX_ARTICLE_AGE_DAYS): continue
        
        articles.append({
            "title": title, 
            "summary": summary[:1500], 
            "link": link, 
            "source": source["name"],
            "published": pub_date
        })
    return articles

# ============ GENERATION ============

async def generate_post(article: Dict, style: Dict) -> Optional[str]:
    full_text = fetch_full_article(article["link"])
    content = full_text if full_text else article["summary"]
    
    prompt = f"""
Ты ведешь Telegram канал про Нейросети и AI. Твоя аудитория - гики и энтузиасты.
НЕ пиши про кибербезопасность, взломы, политику. Пиши про технологии.

Источник: {article['source']}
Заголовок: {article['title']}
Текст: {content}

ТВОЯ ЗАДАЧА:
Напиши короткий, емкий пост (до 700 знаков).
1. О чем речь (суть новинки/открытия)?
2. Почему это круто?
3. Закончи мысль (не обрывай текст).

Стиль: {style['tone']}
Эмодзи: используй 1-3 шт.
Язык: Русский.
"""
    
    response_text = None

    # 1. Пробуем Copilot SDK
    if USE_COPILOT_SDK and copilot_client:
        try:
            session = copilot_client.create_session(system="Ты эксперт по AI.", temperature=0.7)
            resp = await session.send_message(prompt)
            response_text = resp.text
        except Exception as e:
            print(f"⚠️ Copilot Error: {e}")

    # 2. Если не вышло - OpenAI
    if not response_text:
        try:
            resp = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=800
            )
            response_text = resp.choices[0].message.content
        except: pass

    if not response_text: return None
    
    # Чистим
    clean = response_text.strip().strip('"').replace("**", "")
    return build_final_post(clean, article["link"])

# ============ IMAGE ============

def generate_image(title: str) -> Optional[str]:
    # Делаем промпт более "футуристичным"
    prompt = f"futuristic ai technology, neural network visualization, {re.sub(r'[^a-zA-Z]', ' ', title)[:40]}, 3d render, 8k, blue and purple neon light"
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?seed={random.randint(0,10**7)}&width=1024&height=1024&nologo=true"
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
        if resp.status_code == 200 and len(resp.content) > 10000:
            fname = f"img_{int(time.time())}.jpg"
            with open(fname, "wb") as f: f.write(resp.content)
            return fname
    except: pass
    return None

def cleanup_image(path):
    if path and os.path.exists(path): os.remove(path)

# ============ MAIN ============

async def autopost():
    state.cleanup_old()
    print("🚀 [AI Bot] Ищем новости про Нейросети...")
    
    all_articles = []
    
    # Ищем подходящую категорию
    target_cat = state.get_next_category()
    print(f"🎯 Категория поиска: {target_cat}")
    
    # Фильтруем источники по категории
    sources = [s for s in RSS_SOURCES if s["category"] == target_cat]
    
    for source in sources:
        print(f"   Сканирую {source['name']}...")
        found = load_rss(source)
        all_articles.extend(found)

    if not all_articles:
        print("❌ Новостей в этой категории нет, пробуем все...")
        for source in RSS_SOURCES:
            all_articles.extend(load_rss(source))
    
    if not all_articles:
        print("💤 Вообще пусто.")
        return

    # Сортируем: свежие сверху
    all_articles.sort(key=lambda x: x["published"], reverse=True)
    
    for article in all_articles[:10]:
        print(f"\n📝 Обработка: {article['title'][:40]}...")
        
        post_text = await generate_post(article, random.choice(POST_STYLES))
        if not post_text: continue
        
        img = generate_image(article["title"])
        try:
            if img: await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
            else: await bot.send_message(CHANNEL_ID, text=post_text)
            
            state.mark_posted(article["title"], article["link"])
            print("✅ ОПУБЛИКОВАНО!")
            cleanup_image(img)
            return # Уходим после 1 поста
        except Exception as e:
            print(f"❌ Ошибка Telegram: {e}")
            cleanup_image(img)

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())



















































































































































































































































