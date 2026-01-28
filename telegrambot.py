import os
import json
import asyncio
import random
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs

import requests
import feedparser
import urllib.parse
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from groq import Groq

# ============ CONFIG ============

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID]):
    print("⚠️ WARNING: Не все ключи найдены в ENV!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
groq_client = Groq(api_key=GROQ_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

POSTED_FILE = "posted_articles.json"
RETENTION_DAYS = 30
TELEGRAM_CAPTION_LIMIT = 1024

# ============ КЛЮЧЕВЫЕ СЛОВА ============

AI_KEYWORDS = [
    "нейросеть", "нейросети", "нейронная сеть", "ии", "искусственный интеллект",
    "neural network", "artificial intelligence",
    "llm", "gpt", "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум", "deepseek", "grok",
    "openai", "anthropic", "deepmind", "сбер ai", "яндекс ai",
    "hugging face", "stability ai", "meta ai", "google ai",
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    "генерация видео", "text-to-image", "text-to-video",
    "машинное обучение", "глубокое обучение", "transformer",
    "трансформер", "языковая модель", "мультимодальный",
    "дообучение", "обучение модели", "датасет", "fine-tuning",
    "чат-бот", "голосовой помощник", "распознавание",
    "нейросетевой", "ai-ассистент", "умный помощник",
    "компьютерное зрение", "обработка языка", "nlp",
    "agi", "рассуждение", "агент", "ai-агент", "контекстное окно",
    "токен", "большая языковая модель", "reasoning",
    "обучение с подкреплением", "rlhf", "промпт", "prompt",
    "алгоритм машинного", "обучение нейросети"
]

EXCLUDE_KEYWORDS = [
    # Финансы и бизнес
    "акции", "акция", "биржа", "котировки", "индекс",
    "инвестиции", "инвестор", "инвесторы", "дивиденды",
    "ipo", "капитализация", "рыночная стоимость",
    "выручка", "прибыль", "убыток", "доход", "оборот",
    "финансовый отчёт", "финансовый отчет", "квартальный отчёт",
    "миллиард долларов", "миллион долларов", "млрд", "млн рублей",
    "курс доллара", "курс евро", "курс рубля", "валюта",
    "цб", "центробанк", "ставка", "ключевая ставка", "инфляция",
    "экономика", "экономический", "ввп", "рецессия",
    "банк", "кредит", "ипотека", "вклад", "депозит",
    "фонд", "венчурный", "раунд финансирования",
    "сделка", "слияние", "поглощение", "m&a",
    "рынок", "доля рынка", "конкуренты",
    "цена акций", "стоимость компании", "оценка компании",
    "выход на биржу", "размещение", "листинг",
    
    # Кадры
    "назначен", "назначение", "отставка", "уволен",
    "генеральный директор", "ceo", "основатель ушёл",
    "сокращение штата", "увольнения", "сокращения",
    "офис", "штаб-квартира", "переезд компании",
    
    # Спорт
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч",
    "олимпиада", "чемпионат", "турнир", "сборная",
    
    # Игры
    "игра", "геймплей", "playstation", "xbox", "steam", "nintendo",
    "видеоигра", "консоль", "gaming",
    
    # Развлечения
    "кино", "фильм", "сериал", "музыка", "концерт", "актёр", "актер",
    "премьера", "трейлер", "netflix", "кинотеатр",
    
    # Политика
    "выборы", "президент", "парламент", "политик", "депутат",
    "санкции", "правительство", "министр", "закон", "законопроект",
    
    # Медицина
    "болезнь", "covid", "пандемия", "грипп", "вакцина",
    
    # Крипта
    "крипто", "bitcoin", "биткойн", "биткоин", "ethereum",
    "nft", "блокчейн", "криптовалюта", "майнинг",
    
    # Юридическое
    "суд", "судебный", "арест", "приговор", "тюрьма", "штраф",
    "иск", "антимонопольный",
    
    # Археология и история
    "археолог", "археология", "археологический", "раскопки",
    "древн", "артефакт", "палеонтолог", "окаменелости",
    "доисторический", "палеолит", "неолит", "мезолит",
    "памятник культуры", "исторический памятник",
    "тысяч лет", "миллион лет", "возраст составляет",
    "обнаружен во время раскопок", "найден при раскопках",
    "античн", "средневеков", "династия", "цивилизация",
    "захоронение", "гробница", "мумия", "саркофаг",
    
    # НОВОЕ: Автомобили и транспорт
    "автомобиль", "автомобил", "машина", "авто", "автопром",
    "электромобиль", "электрокар", "электромобил",
    "tesla", "тесла", "bmw", "mercedes", "audi", "volkswagen",
    "toyota", "honda", "ford", "chevrolet", "nissan",
    "двигатель", "мотор", "коробка передач", "трансмиссия",
    "бензин", "дизель", "заправка", "топливо",
    "кроссовер", "седан", "хэтчбек", "внедорожник", "суv",
    "пробег", "расход топлива", "разгон", "лошадиных сил",
    "запас хода", "батарея", "аккумулятор электромобиля",
    "зарядная станция", "зарядка электромобиля",
    "автосалон", "дилер", "тест-драйв",
    "пдд", "гибдд", "штраф за", "дорожн",
    "парковка", "стоянка", "гараж",
    "шины", "резина", "колёса", "диски",
    "кузов", "салон автомобиля", "багажник",
    "руль", "педаль", "тормоз",
    "geely", "haval", "chery", "lada", "уаз",
    "lamborghini", "ferrari", "porsche", "maserati",
    "electric vehicle", "ev", "hybrid", "гибрид"
]

BAD_PHRASES = [
    "предлагает решение", "предлагает уникальное решение",
    "обеспечивает высококачественную защиту", "обеспечивает надёжную защиту",
    "обеспечивает защиту", "позволяет сосредоточиться на своих задачах",
    "позволяет не думать об угрозах", "делает бизнес устойчивее",
    "позволяет бизнесу работать устойчивее", "значительно упрощает",
    "кардинально упрощает", "комплексное решение для",
    "идеальное решение для", "помогает бизнесу эффективнее работать",
]

def is_too_promotional(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in BAD_PHRASES):
        return True
    if ("обеспечивает" in low or "позволяет" in low or "предлагает решение" in low) and \
       not any(k in low for k in ["за счёт", "за счет", "используя", "через", "например", 
                                   "в том числе", "фильтрации", "анализ трафика", 
                                   "rate limiting", "балансировщик"]):
        return True
    return False


# ============ URL NORMALIZATION ============

def normalize_url(url: str) -> str:
    if not url:
        return ""
    
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        domain = parsed.netloc.lower().replace("www.", "")
        normalized = f"{domain}{path}"
        return normalized
    except Exception:
        url = url.replace("https://", "").replace("http://", "")
        url = url.replace("www.", "")
        url = url.split("?")[0].split("#")[0]
        return url.rstrip("/").lower()


def extract_article_id(url: str) -> str:
    normalized = normalize_url(url)
    
    habr_match = re.search(r'habr\.com/.+?/(\d{5,7})', normalized)
    if habr_match:
        return f"habr_{habr_match.group(1)}"
    
    dnews_match = re.search(r'3dnews\.ru/(\d+)', normalized)
    if dnews_match:
        return f"3dnews_{dnews_match.group(1)}"
    
    if 'ixbt.com' in normalized:
        return f"ixbt_{hashlib.md5(normalized.encode()).hexdigest()[:12]}"
    
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


# ============ POSTED ARTICLES MANAGER ============

class PostedManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.posted_ids: set = set()
        self.posted_urls: set = set()
        self.data: list = []
        self._load()
    
    def _load(self):
        print(f"\n{'='*50}")
        print(f"📂 Загрузка истории: {self.filepath}")
        
        if not os.path.exists(self.filepath):
            print("   ⚠️ Файл не найден, создаём новый")
            self._save()
            return
        
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            
            if not isinstance(self.data, list):
                print("   ⚠️ Неверный формат, сбрасываем")
                self.data = []
                return
            
            for item in self.data:
                if isinstance(item, dict) and "id" in item:
                    url = item["id"]
                    self.posted_urls.add(normalize_url(url))
                    self.posted_ids.add(extract_article_id(url))
            
            print(f"   ✅ Загружено: {len(self.data)} записей")
            print(f"   📊 Уникальных ID: {len(self.posted_ids)}")
            
        except json.JSONDecodeError as e:
            print(f"   ❌ Ошибка JSON: {e}")
            self.data = []
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            self.data = []
    
    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            print(f"💾 Сохранено: {len(self.data)} записей")
        except Exception as e:
            print(f"❌ Ошибка сохранения: {e}")
    
    def is_posted(self, url: str) -> bool:
        article_id = extract_article_id(url)
        if article_id in self.posted_ids:
            return True
        
        normalized = normalize_url(url)
        if normalized in self.posted_urls:
            return True
        
        return False
    
    def add(self, url: str, title: str = ""):
        article_id = extract_article_id(url)
        normalized = normalize_url(url)
        
        self.posted_ids.add(article_id)
        self.posted_urls.add(normalized)
        
        self.data.append({
            "id": url,
            "article_id": article_id,
            "title": title[:100] if title else "",
            "timestamp": datetime.now().timestamp()
        })
        
        self._save()
        print(f"   📝 Добавлено: {article_id}")
    
    def cleanup(self, days: int = 30):
        if not self.data:
            return
        
        cutoff = datetime.now().timestamp() - (days * 86400)
        old_count = len(self.data)
        
        self.data = [
            item for item in self.data
            if item.get("timestamp") is None or item.get("timestamp", 0) > cutoff
        ]
        
        removed = old_count - len(self.data)
        if removed > 0:
            self.posted_ids.clear()
            self.posted_urls.clear()
            for item in self.data:
                if "id" in item:
                    self.posted_urls.add(normalize_url(item["id"]))
                    self.posted_ids.add(extract_article_id(item["id"]))
            
            self._save()
            print(f"🧹 Очищено: {removed} старых записей")
    
    def count(self) -> int:
        return len(self.data)


posted = PostedManager(POSTED_FILE)


# ============ HELPERS ============

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())

def detect_topic(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if any(kw in text for kw in ["gpt", "chatgpt", "claude", "llm", "языковая модель"]):
        return "llm"
    elif any(kw in text for kw in ["midjourney", "dall-e", "stable diffusion", "генерация изображ"]):
        return "image_gen"
    elif any(kw in text for kw in ["робот", "robot", "автономн"]):
        return "robotics"
    elif any(kw in text for kw in ["nvidia", "gpu", "процессор", "чип"]):
        return "hardware"
    else:
        return "ai"

def get_hashtags(topic: str) -> str:
    hashtag_map = {
        "llm": "#ChatGPT #LLM #нейросети",
        "image_gen": "#AI #генерация #нейросети",
        "robotics": "#роботы #AI #технологии",
        "hardware": "#железо #GPU #технологии",
        "ai": "#AI #нейросети #технологии",
    }
    return hashtag_map.get(topic, "#AI #нейросети #технологии")

def ensure_complete_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if text[-1] in '.!?':
        return text
    last_end = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
    if last_end > 0:
        return text[:last_end + 1]
    return text + '.'

def trim_core_text_to_limit(core_text: str, max_core_length: int) -> str:
    core_text = core_text.strip()
    if len(core_text) <= max_core_length:
        return ensure_complete_sentence(core_text)
    
    sentences = re.split(r'(?<=[.!?])\s+', core_text)
    result = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = (result + " " + sentence).strip() if result else sentence
        if len(candidate) <= max_core_length:
            result = candidate
        else:
            break
    
    if not result and sentences:
        result = sentences[0][:max_core_length]
        if ' ' in result:
            result = result.rsplit(' ', 1)[0]
    
    return ensure_complete_sentence(result)

def build_final_post(core_text: str, hashtags: str, link: str, max_total: int = 1024) -> str:
    cta_line = "\n\n🔥 — огонь! | 🗿 — ну такое | ⚡ — буду пользоваться"
    source_line = f'\n🔗 <a href="{link}">Источник</a>'
    hashtag_line = f"\n\n{hashtags}"
    
    service_length = len(cta_line) + len(hashtag_line) + len(source_line)
    max_core_length = max_total - service_length - 10
    
    trimmed_core = trim_core_text_to_limit(core_text, max_core_length)
    return trimmed_core + cta_line + hashtag_line + source_line


# ============ PARSERS ============

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"   ⚠️ RSS недоступен: {source}")
            return articles
    except Exception as e:
        print(f"   ❌ Ошибка RSS {source}: {e}")
        return articles

    new_count = 0
    skip_count = 0
    
    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        if not link:
            continue
        
        title = clean_text(entry.get("title") or "")
        
        if posted.is_posted(link):
            skip_count += 1
            continue
        
        new_count += 1
        articles.append({
            "id": link,
            "title": title,
            "summary": clean_text(entry.get("summary") or entry.get("description") or "")[:700],
            "link": link,
            "source": source,
            "published_parsed": datetime.now()
        })
    
    print(f"   📰 {source}: +{new_count} новых, ⏭️{skip_count} пропущено")
    return articles

def load_articles_from_sites() -> List[Dict]:
    print("\n🔄 Загрузка RSS лент...")
    articles: List[Dict] = []
    
    # Только специализированные AI-хабы Habr
    articles.extend(load_rss("https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "Habr AI"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "Habr ML"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru", "Habr Neural"))
    
    # Общие техно-ленты (с жёсткой фильтрацией)
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    
    print(f"\n📊 Всего новых статей: {len(articles)}")
    return articles

def filter_articles(articles: List[Dict]) -> List[Dict]:
    """
    СТРОГАЯ ФИЛЬТРАЦИЯ:
    1. Исключаем нежелательные темы (авто, археология, финансы и т.д.)
    2. Оставляем ТОЛЬКО статьи с AI-ключевыми словами
    """
    valid = []
    filtered_out = {"exclude": 0, "no_ai": 0}
    debug_excluded = []  # Для отладки
    
    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()
        
        # Шаг 1: Исключаем нежелательные темы
        excluded_kw = next((kw for kw in EXCLUDE_KEYWORDS if kw in text), None)
        if excluded_kw:
            filtered_out["exclude"] += 1
            debug_excluded.append(f"{e['title'][:50]}... (исключено: '{excluded_kw}')")
            continue
        
        # Шаг 2: Оставляем ТОЛЬКО AI-тематику
        if not any(kw in text for kw in AI_KEYWORDS):
            filtered_out["no_ai"] += 1
            debug_excluded.append(f"{e['title'][:50]}... (нет AI-слов)")
            continue
        
        valid.append(e)
    
    # Вывод отладочной информации
    if debug_excluded:
        print(f"\n🔍 Примеры отфильтрованных статей:")
        for ex in debug_excluded[:5]:  # Показываем первые 5
            print(f"   ❌ {ex}")
    
    print(f"\n❌ Отфильтровано: {filtered_out['exclude']} (исключения), {filtered_out['no_ai']} (не AI)")
    print(f"🎯 После фильтрации (AI-тематика): {len(valid)}")
    
    valid.sort(key=lambda x: x["published_parsed"], reverse=True)
    return valid


# ============ GROQ ============

def build_dynamic_prompt(title: str, summary: str) -> str:
    return f"""
Ты — дружелюбный автор канала про AI-технологии и нейросети.
Твоя задача: Написать подробный и увлекательный пост про ИИ.

НОВОСТЬ:
Заголовок: {title}

Текст: {summary}

ТРЕБОВАНИЯ К ТЕКСТУ:
1. НАЧАЛО: Обязательно начни с фразы "Всем привет! 👋" или "Привет, друзья! ✌️".
2. СТИЛЬ: 
   - Пиши живым языком, как будто рассказываешь другу.
   - Не используй сухой "новостной" стиль. 
   - Не используй рекламный стиль.
   - Избегай сложных причастий, пиши просто.
3. СОДЕРЖАНИЕ:
   - Объясни суть: что именно произошло в мире AI?
   - Как эта технология работает?
   - Зачем это нужно пользователям?
4. ОБЪЕМ: 1000-1200 знаков.

ЗАПРЕТЫ:
- Не используй слова: "революционный", "беспрецедентный", "покупайте", "подписывайтесь".
- Не шути про восстание машин и Skynet.
- Не упоминай автомобили, если это не про AI в автопилотах.
"""

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    print(f"   📝 Генерация текста...")
    
    try:
        res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": build_dynamic_prompt(title, summary)}],
            temperature=0.7,
            max_tokens=1000,
        )
        core = res.choices[0].message.content.strip()
        
        if core.startswith('"') and core.endswith('"'):
            core = core[1:-1]
        
        if is_too_promotional(core):
            print("   ⚠️ Текст рекламный, пропуск")
            return None
        
        topic = detect_topic(title, summary)
        return build_final_post(core, get_hashtags(topic), link, TELEGRAM_CAPTION_LIMIT)
    
    except Exception as e:
        print(f"   ❌ Groq ошибка: {e}")
        return None


# ============ IMAGE ============

def generate_image(title: str, max_retries: int = 2) -> Optional[str]:
    styles = [
        "minimalist technology illustration, clean lines, white background, vector art",
        "abstract neural network visualization, connecting dots, blue gradient",
        "isometric 3d icon of AI, glass texture, soft studio lighting",
    ]
    
    for attempt in range(max_retries):
        seed = random.randint(0, 10**7)
        clean_title = re.sub(r'[^a-zA-Z0-9\s]', '', title)[:50]
        prompt = f"{random.choice(styles)}, {clean_title}"
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024&nologo=true"
        
        try:
            print(f"   🎨 Генерация картинки ({attempt+1}/{max_retries})...")
            resp = requests.get(url, timeout=40, headers=HEADERS)
            if resp.status_code == 200 and len(resp.content) > 10000:
                fname = f"img_{seed}.jpg"
                with open(fname, "wb") as f:
                    f.write(resp.content)
                return fname
        except Exception as e:
            print(f"   ⚠️ Ошибка: {e}")
    
    return None

def cleanup_image(filepath: Optional[str]):
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except:
            pass


# ============ MAIN ============

async def autopost():
    print("\n" + "="*60)
    print(f"🚀 СТАРТ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 В истории: {posted.count()} статей")
    print("="*60)
    
    posted.cleanup(RETENTION_DAYS)
    
    articles = load_articles_from_sites()
    candidates = filter_articles(articles)
    
    if not candidates:
        print("\n❌ Нет подходящих новостей про AI")
        return
    
    art = candidates[0]
    article_id = extract_article_id(art["link"])
    
    print(f"\n🎯 Выбрана статья:")
    print(f"   ID: {article_id}")
    print(f"   Заголовок: {art['title'][:60]}...")
    print(f"   URL: {art['link']}")
    
    post_text = short_summary(art["title"], art["summary"], art["link"])
    
    if not post_text:
        print("\n⚠️ Не удалось сгенерировать текст")
        return
    
    img = generate_image(art["title"])
    
    try:
        if img:
            await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
        else:
            await bot.send_message(CHANNEL_ID, text=post_text, disable_web_page_preview=False)
        
        posted.add(art["link"], art["title"])
        
        print(f"\n✅ ОПУБЛИКОВАНО!")
        print(f"📊 Теперь в истории: {posted.count()} статей")
        
    except Exception as e:
        print(f"\n❌ Ошибка Telegram: {e}")
    finally:
        cleanup_image(img)


async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()
    print("\n" + "="*60)
    print("✅ ЗАВЕРШЕНО")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())























































































































































































































































