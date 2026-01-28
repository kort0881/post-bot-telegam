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
import requests
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

# Загружаем конфиг
config = Config.from_env()

bot = Bot(
    token=config.telegram_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
groq_client = Groq(api_key=config.groq_api_key)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# ============ RSS ИСТОЧНИКИ ============

RSS_FEEDS = [
    ("https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "Habr AI"),
    ("https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "Habr ML"),
    ("https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru", "Habr Neural"),
    ("https://3dnews.ru/news/rss/", "3DNews"),
    ("https://www.ixbt.com/export/news.rss", "iXBT"),
]

# ============ КЛЮЧЕВЫЕ СЛОВА ============

AI_KEYWORDS = [
    # Общие термины
    "нейросеть", "нейросети", "нейронная сеть", "нейросетевой",
    "искусственный интеллект", "ии",
    "neural network", "artificial intelligence",
    
    # Модели и продукты
    "llm", "gpt", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум", "deepseek", "grok",
    
    # Компании
    "openai", "anthropic", "deepmind", "сбер ai", "яндекс ai",
    "hugging face", "stability ai", "meta ai", "google ai",
    
    # Генерация
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    "text-to-image", "text-to-video",
    
    # Технические термины
    "машинное обучение", "глубокое обучение", "transformer",
    "трансформер", "языковая модель", "мультимодальный",
    "дообучение", "обучение модели", "датасет", "fine-tuning",
    
    # Применение
    "чат-бот", "голосовой помощник", "распознавание",
    "ai-ассистент", "умный помощник",
    "компьютерное зрение", "обработка языка", "nlp",
    
    # Продвинутые концепции
    "agi", "рассуждение", "агент", "ai-агент", "контекстное окно",
    "токен", "большая языковая модель", "reasoning",
    "обучение с подкреплением", "rlhf", "промпт", "prompt",
    "алгоритм машинного", "обучение нейросети",
]

EXCLUDE_KEYWORDS = [
    # Финансы
    "акции", "биржа", "котировки", "индекс", "инвестиции", "инвестор",
    "дивиденды", "капитализация", "выручка", "прибыль", "убыток",
    "доход", "оборот", "отчётность",
    "центробанк", "ставка", "инфляция", "рецессия",
    "банк", "кредит", "ипотека", "вклад", "депозит", "сделка", "слияние",
    "поглощение", "листинг",
    
    # Кадры
    "назначен", "назначение", "отставка", "уволен",
    "штат", "увольнения", "сокращения", "штаб-квартира",
    
    # Спорт
    "футбол", "хоккей", "спорт", "матч", "турнир",
    "чемпионат", "олимпиада", "сборная",
    
    # Политика
    "выборы", "президент", "депутат", "санкции",
    "тюрьма", "штраф", "приговор", "арест",
    "министр", "правительство", "госдума",
    
    # Развлечения
    "кино", "фильм", "сериал", "концерт", "актер", "актёр",
    "режиссер", "премьера", "кинотеатр",
    
    # Авто
    "автомобиль", "машина", "тесла", "tesla",
    "электромобиль", "автопилот", "двигатель",
    "бензин", "дизель", "водитель", "автопром",
    
    # Археология/История
    "археолог", "раскопки", "древний", "артефакт", "мумия",
    "гробница", "динозавр", "ископаемое",
]

BAD_PHRASES = [
    "предлагает решение", "уникальное решение",
    "обеспечивает защиту", "позволяет сосредоточиться",
    "делает бизнес устойчивее", "значительно упрощает",
    "идеальное решение для", "эффективнее работать",
    "на правах рекламы", "партнерский материал",
    "лучшее решение", "революционный продукт",
    "не имеет аналогов", "лидер рынка",
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
    def detect(cls, title: str, summary: str) -> str:
        text = f"{title} {summary}".lower()
        
        if any(kw in text for kw in ["gpt", "chatgpt", "claude", "llm", "gemini"]):
            return cls.LLM
        if any(kw in text for kw in ["midjourney", "dall-e", "stable diffusion", "генерация изображ"]):
            return cls.IMAGE_GEN
        if any(kw in text for kw in ["робот", "robot", "робототехник"]):
            return cls.ROBOTICS
        if any(kw in text for kw in ["nvidia", "gpu", "чип", "процессор", "видеокарт"]):
            return cls.HARDWARE
        
        return cls.AI
    
    @classmethod
    def get_hashtags(cls, topic: str) -> str:
        return cls.HASHTAGS.get(topic, cls.HASHTAGS[cls.AI])


# ============ URL NORMALIZATION ============

def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        domain = parsed.netloc.lower().replace("www.", "")
        return f"{domain}{path}"
    except Exception:
        return url.split("?")[0].rstrip("/")


def extract_article_id(url: str) -> str:
    normalized = normalize_url(url)
    
    # Habr
    habr_match = re.search(r'habr\.com/.+?/(\d{5,7})', normalized)
    if habr_match:
        return f"habr_{habr_match.group(1)}"
    
    # 3DNews
    dnews_match = re.search(r'3dnews\.ru/(\d+)', normalized)
    if dnews_match:
        return f"3dnews_{dnews_match.group(1)}"
    
    # iXBT
    if 'ixbt.com' in normalized:
        return f"ixbt_{hashlib.md5(normalized.encode()).hexdigest()[:12]}"
    
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


# ============ POSTED ARTICLES MANAGER ============

class PostedManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.posted_ids: set = set()
        self.posted_urls: set = set()
        self.data: List[Dict] = []
        self._load()
    
    def _load(self) -> None:
        if not os.path.exists(self.filepath):
            self._save()
            return
        
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            
            for item in self.data:
                if "id" in item:
                    url = item["id"]
                    self.posted_urls.add(normalize_url(url))
                    self.posted_ids.add(extract_article_id(url))
        except json.JSONDecodeError as e:
            print(f"⚠️ Ошибка чтения {self.filepath}: {e}")
            self.data = []
        except Exception as e:
            print(f"⚠️ Неожиданная ошибка загрузки: {e}")
            self.data = []
    
    def _save(self) -> None:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ Ошибка сохранения {self.filepath}: {e}")
    
    def is_posted(self, url: str) -> bool:
        return (
            extract_article_id(url) in self.posted_ids or 
            normalize_url(url) in self.posted_urls
        )
    
    def add(self, url: str, title: str = "") -> None:
        self.posted_ids.add(extract_article_id(url))
        self.posted_urls.add(normalize_url(url))
        self.data.append({
            "id": url,
            "title": title[:100],
            "timestamp": datetime.now().timestamp()
        })
        self._save()
    
    def cleanup(self, days: int = 30) -> int:
        cutoff = datetime.now().timestamp() - (days * 86400)
        old_count = len(self.data)
        self.data = [i for i in self.data if i.get("timestamp", 0) > cutoff]
        removed = old_count - len(self.data)
        
        if removed > 0:
            # Перестраиваем индексы
            self.posted_ids.clear()
            self.posted_urls.clear()
            for item in self.data:
                if "id" in item:
                    url = item["id"]
                    self.posted_urls.add(normalize_url(url))
                    self.posted_ids.add(extract_article_id(url))
            self._save()
            print(f"🧹 Удалено {removed} старых записей")
        
        return removed
    
    def count(self) -> int:
        return len(self.data)


# ============ KEYWORD MATCHING ============

def has_exact_keyword(text: str, keywords: List[str]) -> Optional[str]:
    """
    Проверяет наличие целого слова/фразы.
    Поддерживает слова с дефисами (ai-ассистент, text-to-image).
    """
    text_lower = text.lower()
    # Захватываем слова с дефисами
    words = set(re.findall(r'\b[\w-]+\b', text_lower))
    
    for kw in keywords:
        kw_lower = kw.lower()
        # Фраза из нескольких слов
        if " " in kw_lower:
            if kw_lower in text_lower:
                return kw
        # Одно слово (возможно с дефисом)
        elif kw_lower in words:
            return kw
    
    return None


def has_ai_keyword(text: str) -> bool:
    """
    Проверяет наличие AI-тематики.
    Для AI используем поиск подстроки (нейросет -> нейросети).
    """
    text_lower = text.lower()
    
    for kw in AI_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    
    return False


def is_too_promotional(text: str) -> bool:
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in BAD_PHRASES)


# ============ HELPERS ============

def clean_text(text: str) -> str:
    """Очищает текст от лишних пробелов и переносов."""
    if not text:
        return ""
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


def build_final_post(
    core_text: str, 
    hashtags: str, 
    link: str, 
    max_total: int = 1024
) -> str:
    """Собирает финальный пост с CTA, хештегами и ссылкой."""
    
    # ========== ИЗМЕНЕНИЕ ЗДЕСЬ ==========
    cta_line = "\n\n🔥 — огонь! | 🗿 — ну такое | ⚡ — прикольно"
    # =====================================
    
    source_line = f'\n🔗 <a href="{link}">Источник</a>'
    hashtag_line = f"\n\n{hashtags}"
    
    # Вычисляем максимальную длину основного текста
    reserved = len(cta_line) + len(hashtag_line) + len(source_line) + 20
    max_core = max_total - reserved
    
    if len(core_text) > max_core:
        core_text = core_text[:max_core]
        # Обрезаем до последнего предложения
        last_punct = max(
            core_text.rfind('.'),
            core_text.rfind('!'),
            core_text.rfind('?')
        )
        if last_punct > max_core // 2:  # Если нашли пунктуацию не слишком рано
            core_text = core_text[:last_punct + 1]
    
    return core_text + cta_line + hashtag_line + source_line


# ============ RSS LOADING ============

async def load_rss_async(
    session: aiohttp.ClientSession, 
    url: str, 
    source: str,
    posted_manager: PostedManager
) -> List[Article]:
    """Асинхронно загружает и парсит RSS-ленту."""
    articles = []
    
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                print(f"⚠️ {source}: HTTP {resp.status}")
                return []
            
            content = await resp.text()
            feed = feedparser.parse(content)
            
            if feed.bozo:
                print(f"⚠️ {source}: RSS parsing issue - {feed.bozo_exception}")
                if not feed.entries:
                    return []
    
    except asyncio.TimeoutError:
        print(f"⚠️ {source}: Timeout")
        return []
    except aiohttp.ClientError as e:
        print(f"❌ {source}: Connection error - {e}")
        return []
    except Exception as e:
        print(f"❌ {source}: Unexpected error - {e}")
        return []
    
    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        if not link or posted_manager.is_posted(link):
            continue
        
        title = clean_text(entry.get("title") or "")
        summary = clean_text(
            entry.get("summary") or entry.get("description") or ""
        )[:700]
        
        if not title:
            continue
        
        articles.append(Article(
            id=link,
            title=title,
            summary=summary,
            link=link,
            source=source,
            published=datetime.now()
        ))
    
    return articles


async def load_all_feeds(posted_manager: PostedManager) -> List[Article]:
    """Загружает все RSS-ленты параллельно."""
    print("\n🔄 Загрузка RSS...")
    
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = [
            load_rss_async(session, url, name, posted_manager)
            for url, name in RSS_FEEDS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    articles = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"❌ {RSS_FEEDS[i][1]}: {result}")
        elif isinstance(result, list):
            articles.extend(result)
            if result:
                print(f"✅ {RSS_FEEDS[i][1]}: {len(result)} статей")
    
    print(f"📊 Всего найдено {len(articles)} новых статей")
    return articles


# ============ FILTERING ============

def filter_articles(articles: List[Article]) -> List[Article]:
    """Фильтрует статьи по ключевым словам."""
    valid = []
    excluded_log = []
    
    for article in articles:
        text = article.get_full_text()
        
        # 1. Проверка исключений (точное совпадение слов)
        bad_word = has_exact_keyword(text, EXCLUDE_KEYWORDS)
        if bad_word:
            excluded_log.append(f"❌ {article.title[:40]}... (слово: '{bad_word}')")
            continue
        
        # 2. Проверка тематики AI
        if not has_ai_keyword(text):
            continue
        
        valid.append(article)
    
    excluded_count = len(articles) - len(valid)
    print(f"\n🗑 Отфильтровано {excluded_count} статей")
    
    if excluded_log:
        print("🔍 Примеры исключенных:")
        for log in excluded_log[:5]:
            print(f"   {log}")
    
    # Сортируем по дате (новые первые)
    valid.sort(key=lambda x: x.published, reverse=True)
    
    return valid


# ============ GROQ GENERATION ============

async def generate_summary(
    article: Article,
    rate_limit_delay: float = 1.0
) -> Optional[str]:
    """Генерирует пост через Groq API."""
    print(f"   📝 Обработка: {article.title[:50]}...")
    
    prompt = f"""
Роль: Технический редактор Telegram-канала об искусственном интеллекте.

Задача: Переписать новость в информативный и увлекательный пост для аудитории, интересующейся AI.

Исходник:
Заголовок: {article.title}
Содержание: {article.summary}

Требования:
1. Начни с приветствия: "Привет! 👋" или "AI-новости ⚡" или "Интересное из мира AI 🤖"
2. Объясни ЧТО произошло и ПОЧЕМУ это важно/интересно
3. Используй простой язык, понятный широкой аудитории
4. НЕ используй маркетинговые клише ("уникальный", "революционный", "лучший")
5. НЕ добавляй призывы к действию ("подписывайтесь", "ставьте лайк")
6. Объем: 600-800 символов

Важно: Если новость НЕ связана с AI/ML/нейросетями/технологиями — ответь ТОЛЬКО словом: SKIP
"""
    
    try:
        # Добавляем задержку для rate limiting
        await asyncio.sleep(rate_limit_delay)
        
        response = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=900,
            )
        )
        
        content = response.choices[0].message.content.strip()
        
        # Проверка на SKIP
        if content.upper().startswith("SKIP") or content.upper() == "SKIP":
            print("   ⚠️ Groq: тема не подходит (SKIP)")
            return None
        
        # Проверка на рекламный текст
        if is_too_promotional(content):
            print("   ⚠️ Текст слишком рекламный")
            return None
        
        # Собираем финальный пост
        topic = Topic.detect(article.title, article.summary)
        hashtags = Topic.get_hashtags(topic)
        
        return build_final_post(
            content, 
            hashtags, 
            article.link, 
            config.caption_limit
        )
    
    except Exception as e:
        print(f"   ❌ Ошибка генерации: {e}")
        return None


# ============ IMAGE GENERATION ============

async def generate_image(title: str) -> Optional[str]:
    """Генерирует изображение через Pollinations.ai."""
    try:
        # Очищаем заголовок для промпта
        clean_title = re.sub(r'[^\w\s]', '', title)[:50]
        prompt = (
            f"futuristic AI technology illustration, {clean_title}, "
            "minimalist design, 4k quality, blue and purple neon lighting, "
            "dark background, tech aesthetic"
        )
        
        seed = random.randint(0, 10000)
        url = (
            f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
            f"?width=1024&height=1024&nologo=true&seed={seed}"
        )
        
        # Асинхронный запрос изображения
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    fname = f"temp_img_{seed}.jpg"
                    content = await resp.read()
                    with open(fname, "wb") as f:
                        f.write(content)
                    print(f"   🖼 Изображение сгенерировано")
                    return fname
                else:
                    print(f"   ⚠️ Ошибка генерации изображения: HTTP {resp.status}")
    
    except asyncio.TimeoutError:
        print("   ⚠️ Timeout при генерации изображения")
    except Exception as e:
        print(f"   ⚠️ Ошибка генерации изображения: {e}")
    
    return None


# ============ POSTING ============

async def post_to_channel(
    article: Article,
    text: str,
    posted_manager: PostedManager
) -> bool:
    """Публикует пост в Telegram-канал."""
    img_path = None
    
    try:
        # Генерируем изображение
        img_path = await generate_image(article.title)
        
        if img_path and os.path.exists(img_path):
            await bot.send_photo(
                config.channel_id,
                photo=FSInputFile(img_path),
                caption=text
            )
        else:
            await bot.send_message(
                config.channel_id,
                text=text,
                disable_web_page_preview=False
            )
        
        # Добавляем в опубликованные
        posted_manager.add(article.link, article.title)
        print(f"✅ УСПЕШНО ОПУБЛИКОВАНО: {article.title[:50]}...")
        return True
    
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")
        return False
    
    finally:
        # Удаляем временное изображение
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
            except Exception:
                pass


# ============ MAIN ============

async def autopost():
    """Основная функция автопостинга."""
    print(f"\n{'='*50}")
    print(f"🚀 ЗАПУСК: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    # Инициализация менеджера постов
    posted_manager = PostedManager(config.posted_file)
    print(f"📁 Загружено {posted_manager.count()} опубликованных статей")
    
    # Очистка старых записей
    posted_manager.cleanup(config.retention_days)
    
    # Загрузка статей
    raw_articles = await load_all_feeds(posted_manager)
    
    if not raw_articles:
        print("❌ Не удалось загрузить статьи из RSS")
        return
    
    # Фильтрация
    candidates = filter_articles(raw_articles)
    
    if not candidates:
        print("❌ Нет подходящих новостей после фильтрации")
        return
    
    print(f"\n🎯 Подходящих кандидатов: {len(candidates)}")
    
    # Пробуем статьи по очереди
    for i, article in enumerate(candidates, 1):
        print(f"\n[{i}/{len(candidates)}] Пробуем: {article.title[:60]}...")
        
        # Генерация текста
        text = await generate_summary(article)
        
        if not text:
            print("   ⏩ Пропускаем, пробуем следующую...")
            continue
        
        # Публикация
        success = await post_to_channel(article, text, posted_manager)
        
        if success:
            break  # Выходим после успешной публикации
        
        # Небольшая пауза перед следующей попыткой
        await asyncio.sleep(2)
    
    else:
        print("\n⚠️ Не удалось опубликовать ни одной статьи")
    
    print(f"\n{'='*50}")
    print(f"🏁 Работа завершена: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")


async def main():
    """Точка входа."""
    try:
        await autopost()
    except KeyboardInterrupt:
        print("\n\n⛔ Прервано пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        raise
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
























































































































































































































































