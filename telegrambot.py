import os
import json
import asyncio
import random
from datetime import datetime
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
RETENTION_DAYS = 7

# ============ ПРИОРИТЕТНЫЕ КЛЮЧЕВЫЕ СЛОВА (ИИ/НЕЙРОСЕТИ) ============

AI_PRIORITY_KEYWORDS = [
    # Общие термины ИИ
    "нейросеть", "нейросети", "нейронная сеть", "ии", "искусственный интеллект",
    # LLM и модели
    "llm", "gpt", "gpt-4", "gpt-5", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум",
    # Компании ИИ
    "openai", "anthropic", "deepmind", "сбер", "яндекс", "sber",
    "yandex", "hugging face", "stability ai", "meta ai",
    # Генеративные модели
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    # ML термины
    "машинное обучение", "глубокое обучение", "transformer",
    "трансформер", "языковая модель", "мультимодальный",
    "дообучение", "обучение модели", "датасет",
    # Применения ИИ
    "чат-бот", "голосовой помощник", "автопилот", "распознавание",
    "нейросетевой", "ai-ассистент", "умный помощник",
    # Новые разработки
    "agi", "рассуждение", "агент", "контекстное окно", "токен",
    "большая языковая модель"
]

# ============ ОБЫЧНЫЕ КЛЮЧЕВЫЕ СЛОВА (остаточный принцип) ============

GENERAL_KEYWORDS = [
    # Анонсы техники
    "представил", "анонсировал", "выпустил", "релиз", "запустил",
    "новинка", "дебют", "презентация",
    # Железо
    "процессор", "чип", "cpu", "gpu", "видеокарта",
    "смартфон", "ноутбук", "гаджет", "робот", "квантовый",
    # Кибербезопасность (остаточный принцип)
    "уязвимость", "взлом", "хакер", "кибератака", "безопасность",
    "патч", "malware", "ransomware", "0-day"
]

# ============ ИСКЛЮЧИТЬ ============

EXCLUDE_KEYWORDS = [
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч",
    "игра", "геймплей", "playstation", "xbox", "steam", "nintendo",
    "кино", "фильм", "сериал", "музыка", "концерт", "актёр", "актер",
    "выборы", "президент", "парламент", "политик", "депутат",
    "болезнь", "вирус", "covid", "пандемия", "грипп",
    "крипто", "bitcoin", "биткойн", "ethereum", "nft", "блокчейн",
    "суд", "судебный", "арест", "приговор"
]

# ============ STATE ============

posted_articles: Dict[str, Optional[float]] = {}

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            posted_articles = {item["id"]: item.get("timestamp") for item in posted_data}
        except Exception:
            posted_articles = {}


def save_posted_articles() -> None:
    data = [{"id": id_str, "timestamp": ts} for id_str, ts in posted_articles.items()]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_old_posts() -> None:
    global posted_articles
    now = datetime.now().timestamp()
    cutoff = now - (RETENTION_DAYS * 86400)
    posted_articles = {
        id_str: ts for id_str, ts in posted_articles.items()
        if ts is None or ts > cutoff
    }
    save_posted_articles()


def save_posted(article_id: str) -> None:
    posted_articles[article_id] = datetime.now().timestamp()
    save_posted_articles()


# ============ HELPERS ============

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


# ============ PARSERS ============

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"❌ Ошибка загрузки RSS {url}: {e}")
        return articles

    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        if not link or link in posted_articles:
            continue
        articles.append({
            "id": link,
            "title": clean_text(entry.get("title") or ""),
            "summary": clean_text(
                entry.get("summary") or entry.get("description") or ""
            )[:700],
            "link": link,
            "source": source,
            "published_parsed": datetime.now()
        })
    return articles


def load_articles_from_sites() -> List[Dict]:
    """
    Загружает статьи ТОЛЬКО с русскоязычных источников.
    Приоритет — ИИ/нейросети.
    """
    articles: List[Dict] = []

    # === ПРИОРИТЕТ: ИИ и нейросети (русскоязычные) ===

    # Habr - Искусственный интеллект
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
        "Habr AI"
    ))

    # Habr - машинное обучение
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
        "Habr ML"
    ))

    # Habr - нейросети
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru",
        "Habr Neural"
    ))

    # Habr - Data Science
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/data_science/all/?fl=ru",
        "Habr DS"
    ))

    # Habr - Natural Language Processing
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/natural_language_processing/all/?fl=ru",
        "Habr NLP"
    ))

    # Habr - Big Data
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/bigdata/all/?fl=ru",
        "Habr BigData"
    ))

    # Tproger - программирование и технологии
    articles.extend(load_rss(
        "https://tproger.ru/feed/",
        "Tproger"
    ))

    # VC.ru - всё (фильтруем по ключевым словам)
    articles.extend(load_rss(
        "https://vc.ru/rss/all",
        "VC.ru"
    ))

    # ТАСС - Наука
    articles.extend(load_rss(
        "https://tass.ru/rss/v2.xml?sections=nauka",
        "ТАСС Наука"
    ))

    # РИА Наука
    articles.extend(load_rss(
        "https://ria.ru/export/rss2/science/index.xml",
        "РИА Наука"
    ))

    # === ОБЩИЕ ТЕХНОЛОГИИ (русскоязычные) ===

    # 3DNews - технологии
    articles.extend(load_rss(
        "https://3dnews.ru/news/rss/",
        "3DNews"
    ))

    # iXBT - железо и технологии
    articles.extend(load_rss(
        "https://www.ixbt.com/export/news.rss",
        "iXBT"
    ))

    # ServerNews
    articles.extend(load_rss(
        "https://servernews.ru/rss",
        "ServerNews"
    ))

    # Хайтек (hightech.fm)
    articles.extend(load_rss(
        "https://hightech.fm/feed",
        "Хайтек"
    ))

    # === ОСТАТОЧНЫЙ ПРИНЦИП: Кибербезопасность ===

    # Xakep
    articles.extend(load_rss(
        "https://xakep.ru/feed/",
        "Xakep"
    ))

    return articles


# ============ ФИЛЬТРАЦИЯ С ПРИОРИТЕТОМ ИИ ============

def filter_articles(articles: List[Dict]) -> List[Dict]:
    """
    Фильтрует статьи с приоритетом на ИИ/нейросети.
    Возвращает сначала ИИ-статьи, потом остальные.
    """
    ai_articles = []
    general_articles = []

    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()

        # Пропускаем исключённые темы
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

        # Проверяем приоритетные ключевые слова (ИИ/нейросети)
        if any(kw in text for kw in AI_PRIORITY_KEYWORDS):
            ai_articles.append(e)
        # Проверяем обычные ключевые слова
        elif any(kw in text for kw in GENERAL_KEYWORDS):
            general_articles.append(e)

    # Сортируем по дате (новые первыми)
    ai_articles.sort(key=lambda x: x["published_parsed"], reverse=True)
    general_articles.sort(key=lambda x: x["published_parsed"], reverse=True)

    # Возвращаем: сначала ИИ статьи, потом остальные
    return ai_articles + general_articles


# ============ OPENAI TEXT ============

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    news_text = f"{title}. {summary}"
    prompt = (
        "Вот текст новости про ИИ/технологии. Сделай короткий обзор для Telegram на русском:\n"
        f"{news_text}\n\n"
        "- Объём: 380–450 символов.\n"
        "- Фокус: Что представили, ключевые возможности и почему это важно.\n"
        "- Стиль: Живой, информативный. Используй 1-2 эмодзи по теме (🤖🧠💡🚀).\n"
        "- Формат: 2-3 ключевые фишки технологии или модели.\n"
        "- В конце: 2-3 релевантных хештега (#AI #нейросети #технологии #ИИ).\n"
        "- Язык: Только русский!\n"
        "- Запрещено: Выдумывать факты, использовать клише типа 'мир не стоит на месте'.\n"
        "- Ссылку и подписи в текст не включай."
    )

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=400,
        )
        core = res.choices[0].message.content.strip()

        src = f"\n\nИсточник: {link}"
        ps = "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        return core + src + ps
    except Exception as e:
        print(f"❌ OpenAI: {e}")
        return None


# ============ КАРТИНКИ ============

def generate_image(title: str) -> Optional[str]:
    seed = random.randint(0, 10**6)
    prompt = (
        f"Futuristic AI technology illustration: {title[:80]}, "
        "neural networks, digital brain, glowing circuits, "
        "clean minimal design, soft blue lighting, 4k, no text."
    )
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            fname = f"img_{seed}.jpg"
            with open(fname, "wb") as f:
                f.write(resp.content)
            return fname
    except Exception as e:
        print(f"❌ Ошибка генерации изображения: {e}")
    return None


# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    articles = load_articles_from_sites()
    candidates = filter_articles(articles)

    if not candidates:
        print("❌ Нет подходящих новостей про ИИ/технологии.")
        return

    # Показываем статистику
    ai_count = sum(1 for a in candidates if any(
        kw in f"{a['title']} {a['summary']}".lower()
        for kw in AI_PRIORITY_KEYWORDS
    ))
    print(f"📊 Найдено: {len(candidates)} статей ({ai_count} про ИИ)")

    for art in candidates[:5]:
        print(f"🔍 Обработка: {art['title'][:60]}...")
        post_text = short_summary(art["title"], art["summary"], art["link"])

        if post_text:
            img = generate_image(art["title"])
            try:
                if img:
                    await bot.send_photo(
                        CHANNEL_ID,
                        photo=FSInputFile(img),
                        caption=post_text
                    )
                    os.remove(img)
                else:
                    await bot.send_message(CHANNEL_ID, text=post_text)

                save_posted(art["id"])
                print(f"✅ Опубликовано: {art['source']}")
                break
            except Exception as e:
                print(f"❌ Ошибка отправки: {e}")
                if img and os.path.exists(img):
                    os.remove(img)


async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())




































































































