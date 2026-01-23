import os
import json
import asyncio
import random
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests
import feedparser
import urllib.parse
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from openai import OpenAI

# Попытка импортировать Copilot SDK
try:
    from github_copilot_sdk import CopilotClient
    COPILOT_SDK_AVAILABLE = True
except ImportError:
    COPILOT_SDK_AVAILABLE = False
    print("⚠️ GitHub Copilot SDK не установлен, используется стандартный OpenAI API")

# ============ CONFIG ============

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
USE_COPILOT_SDK = os.getenv("USE_COPILOT_SDK", "false").lower() == "true"

if not all([OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID]):
    raise ValueError("❌ Не все ENV переменные установлены!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Инициализация Copilot SDK
copilot_client = None
if COPILOT_SDK_AVAILABLE and USE_COPILOT_SDK:
    try:
        copilot_client = CopilotClient()
        print("✅ GitHub Copilot SDK инициализирован")
    except Exception as e:
        print(f"⚠️ Не удалось инициализировать Copilot SDK: {e}")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

CACHE_DIR = os.getenv("CACHE_DIR", "cache_tech")
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "state_v2.json") # Новая версия файла состояния
FAILED_FILE = os.path.join(CACHE_DIR, "failed_attempts.json")

RETENTION_DAYS = 60 # Храним историю 2 месяца, чтобы наверняка не повторять
MAX_ARTICLE_AGE_DAYS = 2 # Берем только совсем свежие новости (было 3)
TELEGRAM_CAPTION_LIMIT = 1024

# ============ КАТЕГОРИИ ИСТОЧНИКОВ ============

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
    {"name": "CyberAlerts", "url": "https://cyberalerts.io/rss/latest-public", "category": "security"},
]

CATEGORY_ROTATION = ["ai", "tech_ru", "ai", "robotics", "ai", "tech_ru", "security"]

# ============ СТИЛИ ПОСТОВ ============

POST_STYLES = [
    {
        "name": "восторженный_гик",
        "intro": "Ты — техно-гик. Расскажи о новинке с энтузиазмом.",
        "tone": "Живой, энергичный",
        "emojis": "🔥🚀💡🤖✨"
    },
    {
        "name": "футурист",
        "intro": "Ты — футуролог. Объясни, как это изменит будущее.",
        "tone": "Вдохновляющий",
        "emojis": "🌟🔮🚀🌍✨"
    },
    {
        "name": "скептик",
        "intro": "Ты — опытный разработчик. Разбери суть без маркетинговой шелухи.",
        "tone": "Спокойный, по фактам",
        "emojis": "🧐⚙️📱📊"
    }
]

# ============ ФИЛЬТРЫ ============

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

def is_source_promotional(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    for pattern in SOURCE_PROMO_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

def is_excluded(title: str, summary: str) -> Tuple[bool, str]:
    text = f"{title} {summary}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return True, f"excluded: {kw}"
    return False, ""

# ============ УЛУЧШЕННОЕ СОСТОЯНИЕ (ПУЛЕНЕПРОБИВАЕМОЕ) ============

class State:
    def __init__(self):
        self.data = {
            "content_hashes": {}, # Хеш (Заголовок+Текст) -> timestamp
            "url_hashes": {},     # Хеш (URL) -> timestamp
            "source_index": 0,
            "last_run": None,
            "failed_attempts": {}
        }
        self._load()
    
    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Миграция со старого формата если нужно
                    if "posted_ids" in loaded:
                        self.data["url_hashes"] = {k: v["ts"] for k, v in loaded["posted_ids"].items()}
                    else:
                        self.data.update(loaded)
                print(f"📂 Загружена история: {len(self.data['content_hashes'])} записей")
            except Exception as e:
                print(f"⚠️ Ошибка загрузки состояния: {e}")
    
    def save(self):
        self.data["last_run"] = datetime.now().isoformat()
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ КРИТИЧЕСКАЯ ОШИБКА СОХРАНЕНИЯ: {e}")
    
    # Генерация хеша только из СМЫСЛА (Заголовок + Текст без знаков препинания)
    def calculate_content_hash(self, title: str, summary: str) -> str:
        # Убираем все кроме букв и цифр, приводим к нижнему регистру
        clean_string = re.sub(r'[^\w]', '', f"{title}{summary}").lower()
        return hashlib.sha256(clean_string.encode()).hexdigest()

    def calculate_url_hash(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def is_duplicate(self, title: str, summary: str, url: str) -> bool:
        content_h = self.calculate_content_hash(title, summary)
        url_h = self.calculate_url_hash(url)
        
        if content_h in self.data["content_hashes"]:
            # print(f"  🔒 Дубликат по контенту: {title[:30]}")
            return True
            
        if url_h in self.data["url_hashes"]:
            # print(f"  🔒 Дубликат по ссылке: {title[:30]}")
            return True
            
        return False
    
    def mark_posted(self, title: str, summary: str, url: str):
        now_ts = datetime.now().timestamp()
        content_h = self.calculate_content_hash(title, summary)
        url_h = self.calculate_url_hash(url)
        
        self.data["content_hashes"][content_h] = now_ts
        self.data["url_hashes"][url_h] = now_ts
        self.save() # Сохраняем НЕМЕДЛЕННО
    
    def cleanup_old(self):
        cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
        # Чистим оба словаря
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

# ============ PARSING & PROCESSING ============

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
    hashtag_map = {
        "ai": "#AI #нейросети #технологии",
        "robotics": "#роботы #технологии #будущее",
        "space": "#космос #технологии",
        "tech": "#технологии #новинки #гаджеты",
        "sensational": "#кибербезопасность #взлом #утечка"
    }
    return hashtag_map.get(topic, "#технологии")

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

# ============ RSS LOAD ============

def fetch_full_article(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']): tag.decompose()
        content = soup.find('div', class_=re.compile(r'article|content|post|entry'))
        if content: return content.get_text(separator='\n', strip=True)[:4000]
        return None
    except: return None

def load_rss(source: Dict) -> List[Dict]:
    articles = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        feed = feedparser.parse(resp.content)
    except: return []
    
    if not feed.entries: return []
    now = datetime.now()
    max_age = timedelta(days=MAX_ARTICLE_AGE_DAYS)
    
    for entry in feed.entries[:30]:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")
        summary = clean_text(entry.get("summary", "") or entry.get("description", ""))

        if not title or not link: continue
        
        # === КРИТИЧЕСКАЯ ПРОВЕРКА ДУБЛИКАТОВ ===
        if state.is_duplicate(title, summary, link):
            continue
            
        # Проверка даты
        pub_date = now
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try: pub_date = datetime(*entry.published_parsed[:6])
            except: pass
        
        if now - pub_date > max_age:
            continue
        
        # Фильтры
        excluded, _ = is_excluded(title, summary)
        if excluded: continue
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
    if not copilot_client: return None
    try:
        full_text = fetch_full_article(article["link"])
        content = full_text[:3000] if full_text else article["summary"]
        
        prompt = f"""
{style['intro']}
Тональность: {style['tone']}

НОВОСТЬ:
Заголовок: {article['title']}
Содержание: {content}

СТРУКТУРА:
1. ЗАХВАТ — интригующее начало
2. СУТЬ — что произошло и детали
3. ВЫВОД — почему это важно

ОБЯЗАТЕЛЬНО:
- Пиши СВОИМИ словами, не копируй исходный текст
- Закончи полным предложением
- Максимум 3 эмодзи: {style['emojis']}
"""
        session = copilot_client.create_session(
            system="Ты — автор Telegram-канала о технологиях.",
            temperature=0.8, # Повышаем креативность
            max_tokens=800
        )
        response = await session.send_message(prompt)
        text = response.text.strip().strip('"')
        
        if len(text) < 150: return None
        return build_final_post(text, article["link"], detect_topic(article["title"], article["summary"]))
    except: return None

def generate_post(article: Dict) -> Optional[str]:
    style = random.choice(POST_STYLES)
    
    if copilot_client and USE_COPILOT_SDK:
        print("  🤖 SDK...")
        res = asyncio.run(generate_post_with_copilot_sdk(article, style))
        if res: return res
    
    full_text = fetch_full_article(article["link"])
    content = full_text[:3000] if full_text else article["summary"]
    
    prompt = f"""
{style['intro']}
Тональность: {style['tone']}

НОВОСТЬ:
Заголовок: {article['title']}
Содержание: {content}

ЗАДАЧА:
Напиши УНИКАЛЬНЫЙ пост об этом событии.
Не пересказывай сухо, а сделай интересный обзор.

ТРЕБОВАНИЯ:
• Объём 600-800 символов
• Не более 3 эмодзи: {style['emojis']}
• Закончи мысль
• Без рекламы
"""
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты — креативный автор техно-блога."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85, # Высокая температура для уникальности
            max_tokens=800,
        )
        text = response.choices[0].message.content.strip().strip('"')
        if len(text) < 150: return None
        return build_final_post(text, article["link"], detect_topic(article["title"], article["summary"]))
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

# ============ IMAGE ============

def generate_image(title: str) -> Optional[str]:
    styles = [
        "cyberpunk style illustration",
        "futuristic 3d render, neon lighting",
        "minimalist tech art, blue and violet",
        "isometric technology concept"
    ]
    style = random.choice(styles)
    clean_title = re.sub(r'["\'\n]', ' ', title)[:50]
    prompt = f"{style}, {clean_title}, high quality, 4k, no text"
    
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?seed={random.randint(0, 10**7)}&width=1024&height=1024&nologo=true"
    
    try:
        resp = requests.get(url, timeout=60, headers=HEADERS)
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
    print("🧠 Загрузка новостей...")
    
    all_articles = []
    for source in RSS_SOURCES:
        all_articles.extend(load_rss(source))
    
    if not all_articles:
        print("❌ Нет новых (не опубликованных ранее) статей")
        return

    # Фильтрация по категориям
    categorized = {"sensational": [], "ai": [], "robotics": [], "tech_ru": [], "security": []}
    
    for art in all_articles:
        topic = detect_topic(art["title"], art["summary"])
        if topic == "sensational": categorized["sensational"].append(art)
        elif art["category"] in categorized: categorized[art["category"]].append(art)
        else: categorized["tech_ru"].append(art)

    # Выбор кандидата
    target_category = "sensational" if categorized["sensational"] else state.get_next_category()
    candidates = categorized.get(target_category, [])
    
    if not candidates:
        for cat in ["ai", "tech_ru"]:
            if categorized[cat]: candidates = categorized[cat]; break
            
    if not candidates:
        print("❌ Нет подходящих статей после фильтрации")
        return

    # Сортировка свежих вперед
    candidates.sort(key=lambda x: x["published"], reverse=True)

    print(f"🔄 Выбрана категория: {target_category} (Доступно: {len(candidates)})")

    for article in candidates[:5]: # Пробуем первые 5
        print(f"\n📰 {article['title'][:50]}...")
        
        # ПОВТОРНАЯ ПРОВЕРКА ДУБЛИКАТОВ ПЕРЕД ГЕНЕРАЦИЕЙ
        if state.is_duplicate(article["title"], article["summary"], article["link"]):
            print("  🔒 Найден дубликат в базе (race condition check)")
            continue

        post_text = generate_post(article)
        if not post_text: continue
        
        img = generate_image(article["title"])
        
        try:
            if img:
                await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
            else:
                await bot.send_message(CHANNEL_ID, text=post_text)
                
            # ВАЖНО: Маркируем как опубликованное СРАЗУ
            state.mark_posted(article["title"], article["summary"], article["link"])
            print("✅ Опубликовано!")
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














































































































































































































































