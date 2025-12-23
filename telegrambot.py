import os
import re
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

# ---------------- CONFIG ----------------

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

# ============ ОБЯЗАТЕЛЬНЫЕ КЛЮЧЕВЫЕ СЛОВА (ФОКУС НА НОВИНКИ) ============

REQUIRE_KEYWORDS = [
    # Глаголы анонсов
    "представил", "анонсировал", "презентация", "выпустил", "новинка",
    "релиз", "release", "unveiled", "launch", "показал", "дебют",
    # Железо и компоненты
    "процессор", "чип", "chip", "cpu", "gpu", "архитектура", "техпроцесс",
    "аккумулятор", "дисплей", "экран", "зарядка", "память", "ram",
    # Категории
    "смартфон", "ноутбук", "гаджет", "девайс", "device", "gadget",
    "робот", "беспилотник", "автопилот", "электромобиль", "vr", "ar",
    # ИИ и Прорывы
    "нейросеть", "ии", "ai", "llm", "gpt", "claude", "модель",
    "космос", "ракета", "квантовый", "ученые", "прорыв", "breakthrough"
]

# ============ РОССИЯ ============

RUSSIA_KEYWORDS = [
    "россия", "рф", "российск", "россий", "москв"
]

# ============ ИСКЛЮЧИТЬ (ОЧИЩЕНО ОТ БРЕНДОВ) ============

EXCLUDE_KEYWORDS = [
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "олимпиад", "матч",
    "игра", "геймплей", "gameplay", "dungeon", "quest", "playstation", "xbox",
    "steam", "шутер", "mmorpg", "моя жизнь", "мой опыт", "как я",
    "кино", "фильм", "сериал", "музыка", "концерт", "актер",
    "выборы", "президент", "парламент", "политик",
    "болезнь", "заболева", "вирус", "covid", "терапия",
    "крипто", "bitcoin", "биткойн", "эфириум",
    "суд", "судебный", "иск", "апелляция"
]

# ---------------- STATE ----------------

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


# ---------------- HELPERS ----------------

def safe_get(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


# ---------------- PARSERS ----------------

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
    articles: List[Dict] = []
    # Основные источники новинок
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://servernews.ru/rss", "ServerNews"))
    articles.extend(load_rss("https://xakep.ru/feed/", "Xakep"))
    return articles


# ============ ФИЛЬТРАЦИЯ ============

def filter_articles(articles: List[Dict]) -> List[Dict]:
    suitable = []
    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue
        # Проверка на наличие хотя бы одного ключевого слова новинки
        if any(kw in text for kw in REQUIRE_KEYWORDS):
            suitable.append(e)

    suitable.sort(key=lambda x: x["published_parsed"], reverse=True)
    return suitable


# ============ OPENAI TEXT (ОБНОВЛЕННЫЙ ПРОМПТ) ============

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    news_text = f"{title}. {summary}"
    prompt = (
        "Вот текст новости. Сделай из него короткий обзор техно-новинки для Telegram на русском:\n"
        f"{news_text}\n\n"
        "- Объём: 380–450 символов.\n"
        "- Фокус: Что именно представили, какие главные характеристики (цифры, возможности) и почему это круто.\n"
        "- Стиль: Живой, но без 'воды'. Используй 1-2 эмодзи по теме.\n"
        "- Формат: Опиши 2-3 ключевые фишки устройства или технологии.\n"
        "- В конце: 2-3 релевантных хештега (например, #новости #технологии #гаджеты).\n"
        "- Запрещено: Выдумывать факты и использовать общие фразы 'мир технологий не стоит на месте'.\n"
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
        f"Digital illustration of a new gadget or technology: {title[:100]}, "
        "high-tech, clean minimal design, soft studio lighting, 4k, no text."
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
        print("Нет подходящих новостей про новинки.")
        return

    for art in candidates[:5]:
        print(f"🔍 Обработка: {art['title']}")
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
                print("✅ Опубликовано!")
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



































































































