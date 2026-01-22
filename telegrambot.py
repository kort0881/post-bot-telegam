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
    raise ValueError("❌ Не все ENV переменные установлены!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

POSTED_FILE = "posted_articles.json"
FAILED_FILE = "failed_attempts.json"
RETENTION_DAYS = 30  # Увеличено с 7 до 30 дней
LAST_CATEGORY_FILE = "last_category.json"
LAST_SECURITY_FILE = "last_security_post.json"

MAX_ARTICLE_AGE_DAYS = 3
TELEGRAM_CAPTION_LIMIT = 1024

# ============ КАТЕГОРИИ ИСТОЧНИКОВ ============

SOURCE_CATEGORIES = {
    "ai": ["Habr AI", "Habr ML", "Habr Neural", "Habr NLP", "Reuters AI", "Futurism AI"],
    "tech_ru": ["CNews", "ComNews", "3DNews", "iXBT", "Habr News"],
    "robotics": ["Habr Robotics"],
    "security": ["SecurityNews", "CyberAlerts"],
}

CATEGORY_ROTATION = ["ai", "tech_ru", "ai", "robotics", "ai", "tech_ru", "security"]

# ============ СТИЛИ ПОСТОВ ============

POST_STYLES = [
    {
        "name": "восторженный_гик",
        "intro": "Ты ведёшь новостной канал про ИИ и технологии. Рассказываешь о новинках с энтузиазмом, но по делу.",
        "tone": "Энергичный, живой. Факты подаёшь интересно, но без преувеличений.",
        "emojis": "🔥🚀💡🤖✨"
    },
    {
        "name": "аналитик",
        "intro": "Ты — технический обозреватель. Разбираешь новости, объясняешь суть и почему это важно.",
        "tone": "Спокойный, вдумчивый. Даёшь контекст и выводы.",
        "emojis": "🧠📊🔬💻⚡"
    },
    {
        "name": "ироничный_обозреватель",
        "intro": "Ты — обозреватель с чувством юмора. Подмечаешь интересное, иногда с лёгкой иронией.",
        "tone": "Живой, местами ироничный, но информативный.",
        "emojis": "👀🎯😏🛠️💫"
    },
    {
        "name": "практик",
        "intro": "Ты — практичный специалист. Объясняешь, что сделали, как работает и кому пригодится.",
        "tone": "Деловой, конкретный, без воды.",
        "emojis": "⚙️✅📱🔧💪"
    },
    {
        "name": "футурист",
        "intro": "Ты — энтузиаст технологий. Показываешь, как новинки меняют мир.",
        "tone": "Вдохновляющий, но опирающийся на факты.",
        "emojis": "🌟🔮🚀🌍✨"
    }
]

POST_STRUCTURES = ["hook_details_conclusion", "problem_solution_impact", "news_analysis"]

# ============ КЛЮЧЕВЫЕ СЛОВА ============

AI_KEYWORDS = [
    "нейросеть", "нейросети", "нейронная сеть", "ии", "искусственный интеллект",
    "neural network", "artificial intelligence",
    "llm", "gpt", "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум", "deepseek", "grok",
    "openai", "anthropic", "deepmind", "hugging face", "stability ai",
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    "машинное обучение", "глубокое обучение", "transformer", "трансформер",
    "языковая модель", "мультимодальный", "дообучение", "fine-tuning",
    "чат-бот", "голосовой помощник", "распознавание", "компьютерное зрение",
    "nlp", "agi", "ai-агент", "большая языковая модель", "reasoning", "rlhf"
]

TECH_KEYWORDS = [
    "представил", "анонсировал", "выпустил", "релиз", "запустил",
    "смартфон", "ноутбук", "гаджет", "устройство", "умные часы",
    "робот", "робототехника", "дрон", "беспилотник", "автопилот",
    "квантовый компьютер", "процессор", "чип", "gpu", "видеокарта",
    "nvidia", "amd", "intel", "apple", "spacex", "starship", "космос",
    "vr", "ar", "meta quest", "apple vision", "электромобиль", "tesla",
    "госкорпорация", "микроэлектроника", "полупроводники", "импортозамещение",
    "байкал", "эльбрус", "мтс", "билайн", "мегафон", "ростелеком",
    "5g", "lte", "роскомнадзор", "vpn", "яндекс", "сбер", "vk",
]

SENSATIONAL_KEYWORDS = [
    "взлом", "взломали", "утечка", "утекли данные", "data leak",
    "ransomware", "выкуп", "шифровальщик", "атака", "кибератака",
    "ddos", "фишинг", "эксплойт", "уязвимость", "0-day", "нулевого дня",
    "breach", "leak", "data breach", "hack", "hacked", "vulnerability",
]

# === ФИЛЬТРЫ ДЛЯ ИСХОДНЫХ НОВОСТЕЙ ===

EXCLUDE_KEYWORDS = [
    "акции", "биржа", "котировки", "инвестиции", "дивиденды", "ipo",
    "выручка", "прибыль", "убыток", "финансовый отчёт",
    "курс доллара", "курс евро", "центробанк", "ключевая ставка",
    "венчурный", "слияние", "поглощение", "листинг",
    "назначен", "отставка", "уволен", "ceo", "сокращение штата",
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч",
    "олимпиада", "чемпионат", "турнир",
    "playstation", "xbox", "steam", "nintendo", "видеоигра",
    "кино", "фильм", "сериал", "музыка", "концерт", "актёр",
    "netflix", "выборы", "президент", "парламент", "политик",
    "санкции", "правительство", "министр", "законопроект",
    "covid", "пандемия", "вакцина",
    "bitcoin", "биткоин", "ethereum", "nft", "блокчейн", "криптовалюта",
    "суд", "судебный", "арест", "приговор", "штраф", "иск"
]

# Рекламные паттерны в ИСХОДНОЙ новости (фильтруем ДО генерации)
SOURCE_PROMO_PATTERNS = [
    r"купи(те)?[\s\.,!]", r"закажи(те)?[\s\.,!]", r"оформи(те)?[\s\.,!]",
    r"скачай(те)?[\s\.,!]", r"попробуй(те)?[\s\.,!]",
    r"скидк[аи]", r"промокод", r"акция\b", r"распродажа",
    r"бесплатн(о|ый|ая)", r"в подарок", r"выгод(а|но)",
    r"\d+%\s*(off|скидк)", r"только сегодня", r"только сейчас",
    r"ограниченн(ое|ая)\s+(время|предложение)",
    r"успей(те)?[\s\.,!]", r"не упусти(те)?", r"не пропусти(те)?",
    r"последний шанс", r"лучш(ая|ее)\s+цена",
    r"предзаказ", r"pre-?order", r"старт продаж",
    r"где купить", r"цена от", r"стоимость от",
    r"рубл(ей|ь)", r"\$\d+", r"€\d+", r"₽\d+",
]

def is_source_promotional(title: str, summary: str) -> bool:
    """Проверяет ИСХОДНУЮ новость на рекламу ДО генерации"""
    text = f"{title} {summary}".lower()
    
    for pattern in SOURCE_PROMO_PATTERNS:
        if re.search(pattern, text):
            return True
    
    # Дополнительные проверки
    promo_indicators = [
        "объявила цену", "назвала цену", "стартуют продажи",
        "открыт предзаказ", "можно купить", "поступил в продажу",
        "доступен для заказа", "появился в продаже",
    ]
    
    for indicator in promo_indicators:
        if indicator in text:
            return True
    
    return False

# ============ STATE ============

posted_articles: Dict[str, Dict] = {}
failed_attempts: Dict[str, int] = {}

# ============ НОВЫЕ ФУНКЦИИ ДЛЯ ДЕДУПЛИКАЦИИ ============

def get_content_hash(title: str, summary: str) -> str:
    """Создает хеш на основе содержимого, а не URL"""
    content = f"{title.lower().strip()} {summary.lower().strip()}"
    # Убираем пунктуацию для лучшего сравнения
    content = re.sub(r'[^\w\s]', '', content)
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def is_already_posted(link: str, title: str, summary: str) -> bool:
    """Проверяет дубликаты по URL И содержимому"""
    # Проверка по URL
    if link in posted_articles:
        return True
    
    # Проверка по содержимому
    content_hash = get_content_hash(title, summary)
    for info in posted_articles.values():
        if info.get("content_hash") == content_hash:
            return True
    
    return False

# Загрузка данных
if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            posted_articles = {
                item["id"]: {
                    "timestamp": item.get("timestamp"),
                    "content_hash": item.get("content_hash", "")
                } 
                for item in posted_data
            }
        except Exception:
            posted_articles = {}

if os.path.exists(FAILED_FILE):
    with open(FAILED_FILE, "r", encoding="utf-8") as f:
        try:
            failed_attempts = json.load(f)
        except Exception:
            failed_attempts = {}

def save_posted_articles() -> None:
    data = [
        {
            "id": id_str, 
            "timestamp": info["timestamp"],
            "content_hash": info["content_hash"]
        } 
        for id_str, info in posted_articles.items()
    ]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_failed_attempts() -> None:
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(failed_attempts, f, ensure_ascii=False, indent=2)

def clean_old_posts() -> None:
    global posted_articles
    now = datetime.now().timestamp()
    cutoff = now - (RETENTION_DAYS * 86400)
    posted_articles = {
        id_str: info for id_str, info in posted_articles.items()
        if info.get("timestamp") is None or info.get("timestamp") > cutoff
    }
    save_posted_articles()

def save_posted(article_id: str, title: str, summary: str) -> None:
    """Сохраняет статью с хешем содержимого"""
    posted_articles[article_id] = {
        "timestamp": datetime.now().timestamp(),
        "content_hash": get_content_hash(title, summary)
    }
    save_posted_articles()

def mark_as_failed(article_id: str) -> None:
    """Помечает статью как неудачную, чтобы не пробовать снова"""
    failed_attempts[article_id] = failed_attempts.get(article_id, 0) + 1
    save_failed_attempts()

# ============ CATEGORY ROTATION ============

def load_last_category() -> Dict:
    if not os.path.exists(LAST_CATEGORY_FILE):
        return {"category": None, "index": 0}
    try:
        with open(LAST_CATEGORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"category": None, "index": 0}

def save_last_category(category: str, index: int) -> None:
    try:
        with open(LAST_CATEGORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"category": category, "index": index}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_next_category() -> tuple:
    data = load_last_category()
    last_index = data.get("index", 0)
    next_index = (last_index + 1) % len(CATEGORY_ROTATION)
    return CATEGORY_ROTATION[next_index], next_index

def load_last_security_ts() -> Optional[float]:
    if not os.path.exists(LAST_SECURITY_FILE):
        return None
    try:
        with open(LAST_SECURITY_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("ts")
    except Exception:
        return None

def save_last_security_ts() -> None:
    try:
        with open(LAST_SECURITY_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now().timestamp()}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ============ HELPERS ============

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())

def get_article_category(source: str) -> str:
    for category, sources in SOURCE_CATEGORIES.items():
        if source in sources:
            return category
    return "tech_ru"

def detect_topic(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()

    if any(kw in text for kw in ["gpt", "chatgpt", "claude", "llm", "языковая модель"]):
        return "llm"
    elif any(kw in text for kw in ["midjourney", "dall-e", "stable diffusion"]):
        return "image_gen"
    elif any(kw in text for kw in ["робот", "robot", "автономн"]):
        return "robotics"
    elif any(kw in text for kw in ["spacex", "космос", "ракета", "спутник"]):
        return "space"
    elif any(kw in text for kw in ["nvidia", "gpu", "процессор", "чип"]):
        return "hardware"
    elif any(kw in text for kw in ["нейросет", "neural", "ai", "искусственный интеллект"]):
        return "ai"
    elif any(kw in text for kw in ["оператор", "тариф", "телеком", "мтс", "билайн"]):
        return "telecom"
    elif any(kw in text for kw in ["госкорпорация", "импортозамещение"]):
        return "ru_tech"
    else:
        return "tech"

def get_hashtags(topic: str) -> str:
    hashtag_map = {
        "llm": "#ChatGPT #LLM #нейросети",
        "image_gen": "#AI #генерация #нейросети",
        "robotics": "#роботы #технологии #будущее",
        "space": "#космос #SpaceX #технологии",
        "hardware": "#железо #GPU #технологии",
        "ai": "#AI #нейросети #технологии",
        "tech": "#технологии #новинки #гаджеты",
        "telecom": "#телеком #связь #операторы",
        "ru_tech": "#импортозамещение #технологии #Россия",
        "sensational": "#кибербезопасность #взлом #утечка"
    }
    return hashtag_map.get(topic, "#технологии #новости")

def force_complete_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    
    incomplete = [
        r'\s+и$', r'\s+а$', r'\s+но$', r'\s+или$', r'\s+что$', r'\s+как$',
        r'\s+для$', r'\s+на$', r'\s+в$', r'\s+с$', r'\s+к$', r'\s+по$',
        r'\s+который$', r'\s+которая$', r'\s+которое$', r'\s+это$',
        r'\s+—$', r'\s+-$', r':$', r';$', r',$',
    ]
    
    for pattern in incomplete:
        text = re.sub(pattern, '', text)
    
    text = text.strip()
    
    if text and text[-1] in '.!?':
        return text
    
    last_end = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
    
    if last_end > len(text) * 0.6:
        return text[:last_end + 1]
    
    return text + '.'

def trim_to_limit(text: str, max_length: int) -> str:
    text = text.strip()
    if len(text) <= max_length:
        return force_complete_sentence(text)
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = ""
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = (result + " " + sentence).strip() if result else sentence
        if len(candidate) <= max_length:
            result = candidate
        else:
            break
    
    if not result and sentences:
        result = sentences[0][:max_length]
        if ' ' in result:
            result = result.rsplit(' ', 1)[0]
    
    return force_complete_sentence(result)

def build_final_post(core_text: str, hashtags: str, link: str) -> str:
    cta = "\n\n👍 — полезно | 👎 — мимо | 🔥 — огонь"
    source = f'\n\n🔗 <a href="{link}">Источник</a>'
    tags = f"\n\n{hashtags}"
    
    service_len = len(cta) + len(source) + len(tags)
    max_core = TELEGRAM_CAPTION_LIMIT - service_len - 5
    
    trimmed = trim_to_limit(core_text, max_core)
    
    return trimmed + cta + tags + source

# ============ PARSERS ============

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"⚠️ RSS недоступен: {source}")
            return articles
    except Exception as e:
        print(f"❌ Ошибка RSS {source}: {e}")
        return articles

    now = datetime.now()
    max_age = timedelta(days=MAX_ARTICLE_AGE_DAYS)

    for entry in feed.entries[:50]:
        link = entry.get("link", "")
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:1000]
        
        if not link:
            continue
        
        # Проверка на дубликаты по URL и содержимому
        if is_already_posted(link, title, summary):
            continue
        
        # Проверка на неудачные попытки
        if link in failed_attempts and failed_attempts[link] >= 3:
            continue

        pub_dt = now
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_dt = datetime(*entry.published_parsed[:6])
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            pub_dt = datetime(*entry.updated_parsed[:6])

        if now - pub_dt > max_age:
            continue

        articles.append({
            "id": link,
            "title": title,
            "summary": summary,
            "link": link,
            "source": source,
            "published_parsed": pub_dt,
            "category": get_article_category(source)
        })

    if articles:
        print(f"✅ {source}: {len(articles)} статей")

    return articles

def load_articles_from_sites() -> List[Dict]:
    articles: List[Dict] = []

    articles.extend(load_rss("https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "Habr AI"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "Habr ML"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru", "Habr Neural"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/natural_language_processing/all/?fl=ru", "Habr NLP"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/robotics/all/?fl=ru", "Habr Robotics"))
    articles.extend(load_rss("https://habr.com/ru/rss/news/?fl=ru", "Habr News"))

    articles.extend(load_rss("https://www.cnews.ru/inc/rss/news.xml", "CNews"))
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://www.comnews.ru/rss", "ComNews"))

    articles.extend(load_rss("https://secnews.ru/rss/", "SecurityNews"))
    articles.extend(load_rss("https://cyberalerts.io/rss/latest-public", "CyberAlerts"))

    articles.extend(load_rss("https://www.reuters.com/technology/artificial-intelligence/rss", "Reuters AI"))
    articles.extend(load_rss("https://futurism.com/categories/ai-artificial-intelligence/feed", "Futurism AI"))

    return articles

def filter_articles(articles: List[Dict]) -> Dict[str, List[Dict]]:
    """Фильтрует статьи: убирает рекламные и нерелевантные ДО генерации"""
    
    categorized = {
        "ai": [],
        "tech_ru": [],
        "robotics": [],
        "security": [],
        "sensational": []
    }
    
    skipped_promo = 0
    skipped_excluded = 0

    for e in articles:
        title = e['title']
        summary = e['summary']
        text = f"{title} {summary}".lower()

        # 1. Исключаем по ключевым словам (политика, финансы и т.д.)
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            skipped_excluded += 1
            continue
        
        # 2. Исключаем рекламные новости
        if is_source_promotional(title, summary):
            skipped_promo += 1
            print(f"  🚫 Реклама: {title[:50]}...")
            continue

        # 3. Категоризация
        if any(kw in text for kw in SENSATIONAL_KEYWORDS):
            categorized["sensational"].append(e)
            continue

        category = e.get("category", "tech_ru")
        
        if any(kw in text for kw in AI_KEYWORDS):
            category = "ai"
        
        if category in categorized:
            categorized[category].append(e)
        else:
            categorized["tech_ru"].append(e)

    # Сортировка по дате
    for cat in categorized:
        categorized[cat].sort(key=lambda x: x["published_parsed"], reverse=True)
        print(f"📂 {cat}: {len(categorized[cat])} статей")
    
    print(f"🚫 Отфильтровано: {skipped_promo} рекламных, {skipped_excluded} нерелевантных")

    return categorized

# ============ ГЕНЕРАЦИЯ ТЕКСТА ============

def build_prompt(title: str, summary: str, style: dict, structure: str) -> str:
    
    structures = {
        "hook_details_conclusion": """
Структура:
1. ЗАХВАТ — интригующее начало, суть новости
2. ПОДРОБНОСТИ — ключевые факты, цифры, детали  
3. ВЫВОД — почему это важно, что это значит
""",
        "problem_solution_impact": """
Структура:
1. КОНТЕКСТ — что было до этого, какую задачу решали
2. РЕШЕНИЕ — что сделали, как это работает
3. ЗНАЧЕНИЕ — какой эффект, что изменится
""",
        "news_analysis": """
Структура:
1. НОВОСТЬ — что произошло
2. ДЕТАЛИ — технические подробности
3. ПЕРСПЕКТИВА — что дальше
"""
    }

    return f"""
{style['intro']}
Тональность: {style['tone']}

НОВОСТЬ:
Заголовок: {title}
Содержание: {summary}

{structures.get(structure, structures['news_analysis'])}

ТРЕБОВАНИЯ:
• Напиши пост на 600-800 символов
• Максимум 2-3 эмодзи из этих: {style['emojis']}
• Пиши живым языком, как для друзей-технарей
• Добавь интересные детали
• Закончи выводом или вопросом
• Текст ДОЛЖЕН заканчиваться на . или ! или ?
• Будь максимально оригинальным в изложении

ЗАПРЕЩЕНО:
• Любые призывы к покупке, заказу, скачиванию
• Слова: скидка, акция, бесплатно, купить, заказать
• Цены и стоимость
• Рекламный тон
• Ссылки и хештеги
• Повторение фраз и структур

Напиши ТОЛЬКО текст поста:
"""


def generate_post_text(title: str, summary: str, link: str) -> Optional[str]:
    style = random.choice(POST_STYLES)
    structure = random.choice(POST_STRUCTURES)
    
    print(f"  📝 Стиль: {style['name']}")
    
    prompt = build_prompt(title, summary, style, structure)
    
    for attempt in range(3):
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — автор Telegram-канала о технологиях. "
                            "Пишешь информативные, оригинальные посты без рекламы. "
                            "Всегда заканчиваешь текст полным предложением. "
                            "Каждый пост должен быть уникальным и не похожим на предыдущие."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.9,  # Увеличено для большего разнообразия
                max_tokens=700,
                frequency_penalty=0.3,  # Штраф за частые повторения
                presence_penalty=0.6,   # Штраф за любые повторения
            )
            
            text = response.choices[0].message.content.strip()
            
            if (text.startswith('"') and text.endswith('"')) or \
               (text.startswith('«') and text.endswith('»')):
                text = text[1:-1].strip()
            
            text = force_complete_sentence(text)
            
            if len(text) < 150:
                print(f"  ⚠️ Попытка {attempt + 1}: короткий текст")
                continue
            
            topic = detect_topic(title, summary)
            if any(kw in (title + summary).lower() for kw in SENSATIONAL_KEYWORDS):
                topic = "sensational"
            
            hashtags = get_hashtags(topic)
            final_post = build_final_post(text, hashtags, link)
            
            print(f"  ✅ Готово: {len(final_post)} символов")
            return final_post
            
        except Exception as e:
            print(f"  ❌ OpenAI ошибка: {e}")
            time.sleep(2)
    
    return None

# ============ ГЕНЕРАЦИЯ КАРТИНОК ============

def generate_image(title: str, max_retries: int = 3) -> Optional[str]:
    styles = [
        "futuristic minimalist illustration, soft gradients",
        "abstract tech visualization, geometric shapes",
        "modern digital art, clean lines, neon accents",
        "sci-fi concept art, atmospheric lighting",
    ]
    
    style = random.choice(styles)
    
    for attempt in range(max_retries):
        seed = random.randint(0, 10**7)
        clean_title = re.sub(r'["\'\n]', ' ', title[:50])
        
        prompt = f"{style}, {clean_title}, technology, 4k, no text, no letters"
        
        try:
            encoded = urllib.parse.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024&nologo=true"
            
            print(f"  🎨 Картинка ({attempt + 1}/{max_retries})...")
            
            resp = requests.get(url, timeout=90, headers=HEADERS)
            
            if resp.status_code == 200 and 'image' in resp.headers.get('content-type', ''):
                if len(resp.content) > 10000:
                    fname = f"img_{seed}.jpg"
                    with open(fname, "wb") as f:
                        f.write(resp.content)
                    print(f"  ✅ Картинка готова")
                    return fname
            
        except Exception as e:
            print(f"  ⚠️ Ошибка: {e}")
        
        time.sleep(2)
    
    return None

def cleanup_image(filepath: Optional[str]) -> None:
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except:
            pass

# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    print("🔄 Загрузка статей...\n")
    
    articles = load_articles_from_sites()
    
    print(f"\n📊 Загружено: {len(articles)} статей")
    print("🔍 Фильтрация...\n")
    
    categorized = filter_articles(articles)
    
    total = sum(len(v) for v in categorized.values())
    if total == 0:
        print("❌ Нет подходящих новостей")
        return
    
    print(f"\n✅ После фильтрации: {total} статей")
    
    last_security_ts = load_last_security_ts()
    now_ts = datetime.now().timestamp()
    security_cooldown = 7 * 86400
    
    posted = False

    # 1. Сенсационные новости
    for art in categorized["sensational"][:10]:
        is_security = art.get("source") in ["SecurityNews", "CyberAlerts"]
        
        if is_security and last_security_ts and (now_ts - last_security_ts) < security_cooldown:
            continue
        
        print(f"\n🚨 СЕНСАЦИЯ: {art['title'][:60]}...")
        
        post_text = generate_post_text(art["title"], art["summary"], art["link"])
        if not post_text:
            print("  ⏭️ Пропускаем, пробуем следующую...")
            mark_as_failed(art["id"])
            continue
        
        img = generate_image(art["title"])
        
        try:
            if img:
                await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
            else:
                await bot.send_message(CHANNEL_ID, text=post_text)
            
            save_posted(art["id"], art["title"], art["summary"])
            if is_security:
                save_last_security_ts()
            
            print(f"✅ Опубликовано!")
            posted = True
            break
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            mark_as_failed(art["id"])
        finally:
            cleanup_image(img)

    # 2. Обычные новости по ротации
    if not posted:
        next_cat, next_idx = get_next_category()
        print(f"\n🔄 Ротация: {next_cat}")
        
        candidates = categorized.get(next_cat, [])
        
        if not candidates:
            for fallback in ["ai", "tech_ru"]:
                if categorized.get(fallback):
                    candidates = categorized[fallback]
                    next_cat = fallback
                    print(f"  ↪️ Fallback: {fallback}")
                    break
        
        # Пробуем до 10 статей из категории
        for art in candidates[:10]:
            print(f"\n📰 {art['title'][:60]}...")
            
            post_text = generate_post_text(art["title"], art["summary"], art["link"])
            if not post_text:
                print("  ⏭️ Пропускаем, пробуем следующую...")
                mark_as_failed(art["id"])
                continue
            
            img = generate_image(art["title"])
            
            try:
                if img:
                    await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
                else:
                    await bot.send_message(CHANNEL_ID, text=post_text)
                
                save_posted(art["id"], art["title"], art["summary"])
                save_last_category(next_cat, next_idx)
                
                print(f"✅ Опубликовано!")
                posted = True
                break
                
            except Exception as e:
                print(f"❌ Ошибка: {e}")
                mark_as_failed(art["id"])
            finally:
                cleanup_image(img)

    if not posted:
        print("\n⚠️ Не удалось опубликовать пост")
    else:
        print("\n🎉 Готово!")

async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())













































































































































































































































