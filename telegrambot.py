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
    # Пытаемся импортировать SDK, как в первом репозитории
    from github_copilot_sdk import CopilotClient
    COPILOT_SDK_AVAILABLE = True
    print("✅ GitHub Copilot SDK найден")
except ImportError:
    COPILOT_SDK_AVAILABLE = False
    print("⚠️ GitHub Copilot SDK не найден (работаем через OpenAI API)")

# ============ CONFIG ============

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
# Включаем SDK, если он доступен и разрешен в настройках
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
STATE_FILE = os.path.join(CACHE_DIR, "state_v2.json")

RETENTION_DAYS = 60
MAX_ARTICLE_AGE_DAYS = 2
TELEGRAM_CAPTION_LIMIT = 1024

# ============ ИСТОЧНИКИ ============

RSS_SOURCES = [
    {"name": "Habr AI", "url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "category": "ai"},
    {"name": "Habr ML", "url": "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "category": "ai"},
    {"name": "NeuroHive", "url": "https://neurohive.io/ru/feed/", "category": "ai"},
    {"name": "Reuters AI", "url": "https://www.reuters.com/technology/artificial-intelligence/rss", "category": "ai"},
    {"name": "Futurism AI", "url": "https://futurism.com/categories/ai-artificial-intelligence/feed", "category": "ai"},
    {"name": "3DNews", "url": "https://3dnews.ru/news/rss/", "category": "tech_ru"},
    {"name": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "category": "tech_ru"},
    {"name": "CNews", "url": "https://www.cnews.ru/inc/rss/news.xml", "category": "tech_ru"},
    {"name": "ComNews", "url": "https://www.comnews.ru/rss", "category": "tech_ru"},
    {"name": "Habr Robotics", "url": "https://habr.com/ru/rss/hub/robotics/all/?fl=ru", "category": "robotics"},
    {"name": "SecurityNews", "url": "https://secnews.ru/rss/", "category": "security"},
]

CATEGORY_ROTATION = ["ai", "tech_ru", "ai", "robotics", "ai", "tech_ru", "security"]

# ============ СТИЛИ ПОСТОВ (AI/TECH) ============

POST_STYLES = [
    {
        "name": "восторженный_гик",
        "intro": "Ты — техно-энтузиаст. Рассказываешь о новинке с драйвом.",
        "tone": "Энергичный, живой",
        "emojis": "🔥🚀💡🤖✨"
    },
    {
        "name": "футурист",
        "intro": "Ты — футуролог. Объясняешь, как это изменит мир.",
        "tone": "Вдохновляющий",
        "emojis": "🌟🔮🚀🌍✨"
    },
    {
        "name": "практик",
        "intro": "Ты — IT-специалист. Объясняешь суть четко и по делу.",
        "tone": "Деловой, конкретный",
        "emojis": "⚙️✅📱💻"
    }
]

# ============ КЛЮЧЕВЫЕ СЛОВА ============

AI_KEYWORDS = [
    "нейросет", "ии", "ai", "gpt", "gemini", "claude", "llama",
    "midjourney", "stable diffusion", "генерация", "чат-бот",
    "deepfake", "deep learning", "машинное обучение", "copilot", 
    "assistant", "sora", "runway", "pika", "hugging face",
    "nvidia", "cuda", "llm", "rag", "интеллект", "openai", "anthropic"
]

SENSATIONAL_KEYWORDS = [
    "взлом", "утечка", "ransomware", "атака", "ddos", "0-day",
    "breach", "leak", "hacked", "уязвимость"
]

EXCLUDE_KEYWORDS = [
    "акции", "биржа", "инвестиц", "квартальный отчет", "ipo",
    "выручка", "прибыль", "убыток", "дивиденды",
    "назначен", "отставка", "уволен", "ceo",
    "футбол", "хоккей", "спорт", "матч", "чемпионат",
    "политика", "выборы", "депутат", "санкции", "закон",
    "суд", "арест", "приговор", "криминал", "убийство",
    "covid", "пандемия", "вакцина"
]

SOURCE_PROMO_PATTERNS = [
    r"купи(те)?[\s\.,!]", r"закажи(те)?[\s\.,!]", 
    r"скидк[аи]", r"промокод", r"акция\b", r"распродажа",
    r"бесплатн(о|ый|ая)", r"выгод(а|но)", r"цена от", 
    r"\d+%\s*(off|скидк)", r"только сегодня",
    r"предзаказ", r"старт продаж", r"где купить"
]

# ============ STATE MANAGEMENT ============

class State:
    def __init__(self):
        self.data = {
            "content_hashes": {}, 
            "url_hashes": {},     
            "source_index": 0,
            "last_run": None
        }
        self._load()
    
    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if "posted_ids" in loaded: # Миграция
                        self.data["url_hashes"] = {k: v.get("ts", 0) for k, v in loaded["posted_ids"].items()}
                    else:
                        self.data.update(loaded)
            except: pass
    
    def save(self):
        self.data["last_run"] = datetime.now().isoformat()
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ Ошибка сохранения state: {e}")
    
    def calculate_content_hash(self, title: str, summary: str) -> str:
        clean = re.sub(r'[^\w]', '', f"{title}{summary}").lower()
        return hashlib.sha256(clean.encode()).hexdigest()

    def calculate_url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def is_duplicate(self, title: str, summary: str, url: str) -> bool:
        if self.calculate_content_hash(title, summary) in self.data["content_hashes"]: return True
        if self.calculate_url_hash(url) in self.data["url_hashes"]: return True
        return False
    
    def mark_posted(self, title: str, summary: str, url: str):
        ts = datetime.now().timestamp()
        self.data["content_hashes"][self.calculate_content_hash(title, summary)] = ts
        self.data["url_hashes"][self.calculate_url_hash(url)] = ts
        self.save() # Сохраняем сразу
    
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

# ============ PARSING ============

def clean_text(text: str) -> str:
    if not text: return ""
    return re.sub(r'<[^>]+>', ' ', text).strip()

def apply_social_disclaimer(text: str) -> str:
    targets = ["instagram", "facebook", "tiktok", "инстаграм", "фейсбук", "тикток", "meta"]
    if any(t in text.lower() for t in targets):
        return text + "\n\n* <i>Instagram, Facebook и TikTok — запрещены или ограничены на территории РФ.</i>"
    return text

def detect_topic(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if any(kw in text for kw in SENSATIONAL_KEYWORDS): return "sensational"
    if any(kw in text for kw in AI_KEYWORDS): return "ai"
    if any(kw in text for kw in ["робот", "robot"]): return "robotics"
    if any(kw in text for kw in ["space", "космос"]): return "space"
    return "tech"

def get_hashtags(topic: str) -> str:
    mapping = {
        "ai": "#AI #нейросети #технологии",
        "robotics": "#роботы #технологии #будущее",
        "space": "#космос #технологии",
        "tech": "#технологии #новинки #гаджеты",
        "sensational": "#кибербезопасность #взлом #утечка"
    }
    return mapping.get(topic, "#технологии")

def build_final_post(text: str, link: str, topic: str) -> str:
    text = apply_social_disclaimer(text)
    hashtags = get_hashtags(topic)
    cta = "\n\n👍 — полезно | 👎 — мимо | 🔥 — огонь"
    source = f'\n\n🔗 <a href="{link}">Источник</a>'
    
    full_post = text + cta + "\n\n" + hashtags + source
    
    if len(full_post) > TELEGRAM_CAPTION_LIMIT:
        cut = TELEGRAM_CAPTION_LIMIT - len(cta) - len(hashtags) - len(source) - 100
        text = text[:cut] + "..."
        text = apply_social_disclaimer(text)
        
    return text + cta + "\n\n" + hashtags + source

def fetch_full_article(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']): tag.decompose()
        content = soup.find('div', class_=re.compile(r'article|content|post|entry'))
        if content: return content.get_text(separator='\n', strip=True)[:4000]
    except: pass
    return None

def load_rss(source: Dict) -> List[Dict]:
    articles = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        feed = feedparser.parse(resp.content)
    except: return []
    
    if not feed.entries: return []
    now = datetime.now()
    
    for entry in feed.entries[:30]:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""))

        if not title or not link: continue
        
        # === ПРОВЕРКА ДУБЛИКАТОВ ===
        if state.is_duplicate(title, summary, link): continue
            
        pub_date = now
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try: pub_date = datetime(*entry.published_parsed[:6])
            except: pass
        
        if now - pub_date > timedelta(days=MAX_ARTICLE_AGE_DAYS): continue
        
        # === ФИЛЬТРЫ ===
        if any(kw in f"{title} {summary}".lower() for kw in EXCLUDE_KEYWORDS): continue
        if is_source_promotional(title, summary): continue
        
        articles.append({
            "title": title,
            "summary": summary[:1500],
            "link": link,
            "source": source["name"],
            "category": source["category"],
            "published": pub_date
        })
    return articles

# ============ GENERATION ============

async def generate_post_with_copilot_sdk(article: Dict, style: Dict) -> Optional[str]:
    """Генерация через SDK (если доступен)"""
    if not copilot_client: return None
    try:
        full_text = fetch_full_article(article["link"])
        content = full_text[:3500] if full_text else article["summary"]
        
        prompt = f"""
{style['intro']}
Тон: {style['tone']}

ЗАГОЛОВОК: {article['title']}
ТЕКСТ: {content}

ЗАДАЧА:
Напиши пост для Telegram (600-800 знаков).
1. ЗАХВАТ ВНИМАНИЯ (без кликбейта)
2. СУТЬ НОВОСТИ (факты)
3. ПОЛЬЗА/ВЫВОД (почему это важно)

ЗАПРЕТЫ:
- Никакой рекламы и призывов купить
- Не обрывай текст
- Используй не более 3 эмодзи: {style['emojis']}
"""
        # Создаем сессию через SDK
        session = copilot_client.create_session(
            system="Ты — лучший техно-блогер Telegram.",
            temperature=0.7,
            max_tokens=900
        )
        response = await session.send_message(prompt)
        text = response.text.strip().strip('"')
        
        if len(text) < 100: return None
        return build_final_post(text, article["link"], detect_topic(article["title"], article["summary"]))
    except Exception as e:
        print(f"⚠️ Ошибка SDK: {e}")
        return None

def generate_post_openai(article: Dict, style: Dict) -> Optional[str]:
    """Генерация через обычный OpenAI (Fallback)"""
    full_text = fetch_full_article(article["link"])
    content = full_text[:3500] if full_text else article["summary"]
    
    prompt = f"""
{style['intro']}
Тон: {style['tone']}

ЗАГОЛОВОК: {article['title']}
ТЕКСТ: {content}

ЗАДАЧА:
Напиши пост для Telegram (600-800 знаков).
Структура: Заголовок-Хук -> Факты -> Вывод.

ЗАПРЕТЫ:
- Не используй слово "шокирующий"
- Не рекламируй
- Используй эмодзи: {style['emojis']}
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=900
        )
        text = response.choices[0].message.content.strip().strip('"')
        if len(text) < 100: return None
        return build_final_post(text, article["link"], detect_topic(article["title"], article["summary"]))
    except Exception as e:
        print(f"❌ Ошибка OpenAI: {e}")
        return None

# ============ IMAGE ============

def generate_image(title: str) -> Optional[str]:
    styles = ["cyberpunk", "futuristic 3d render", "neon tech", "isometric ai"]
    prompt = f"{random.choice(styles)}, {re.sub(r'[^a-zA-Z]', ' ', title)[:50]}, 4k, no text"
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?seed={random.randint(0,10**7)}&width=1024&height=1024&nologo=true"
    
    try:
        resp = requests.get(url, timeout=40, headers=HEADERS)
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
    print("🧠 [TechBot] Старт...")
    
    if USE_COPILOT_SDK: print("🤖 Режим: Copilot SDK")
    else: print("🔧 Режим: OpenAI Fallback")

    all_articles = []
    for source in RSS_SOURCES:
        all_articles.extend(load_rss(source))
    
    if not all_articles:
        print("❌ Нет новых статей")
        return

    # Категоризация
    cats = {"sensational": [], "ai": [], "robotics": [], "tech_ru": [], "security": []}
    for art in all_articles:
        topic = detect_topic(art["title"], art["summary"])
        if topic == "sensational": cats["sensational"].append(art)
        elif art["category"] in cats: cats[art["category"]].append(art)
        else: cats["tech_ru"].append(art)

    # Выбор
    target = "sensational" if cats["sensational"] else state.get_next_category()
    candidates = cats.get(target, []) or cats["ai"] or cats["tech_ru"]
    
    if not candidates: return
    candidates.sort(key=lambda x: x["published"], reverse=True)

    for article in candidates[:5]:
        print(f"\n📰 {article['title'][:50]}...")
        if state.is_duplicate(article["title"], article["summary"], article["link"]): continue
        
        style = random.choice(POST_STYLES)
        
        # Пробуем SDK, если не вышло -> OpenAI
        post_text = None
        if USE_COPILOT_SDK:
            post_text = await generate_post_with_copilot_sdk(article, style)
        
        if not post_text:
            post_text = generate_post_openai(article, style)
            
        if not post_text: continue
        
        img = generate_image(article["title"])
        try:
            if img: await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
            else: await bot.send_message(CHANNEL_ID, text=post_text)
            
            state.mark_posted(article["title"], article["summary"], article["link"])
            print("✅ Успех!")
            cleanup_image(img)
            return
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            cleanup_image(img)

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())















































































































































































































































