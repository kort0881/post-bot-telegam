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

# ============ КЛЮЧЕВЫЕ СЛОВА ============

REQUIRE_KEYWORDS = [
    "представил", "анонсировал", "презентация", "выпустил", "новинка",
    "релиз", "release", "unveiled", "launch", "показал", "дебют",
    "процессор", "чип", "chip", "cpu", "gpu", "архитектура", "техпроцесс",
    "аккумулятор", "дисплей", "экран", "зарядка", "память", "ram",
    "смартфон", "ноутбук", "гаджет", "девайс", "device", "gadget",
    "робот", "беспилотник", "автопилот", "электромобиль", "vr", "ar",
    "нейросеть", "ии", "ai", "llm", "gpt", "claude", "модель",
    "космос", "ракета", "квантовый", "ученые", "прорыв", "breakthrough"
]

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
# Формат: {"article_id": {"timestamp": float, "message_id": int}, ...}

posted_articles: Dict[str, Dict] = {}

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            # Поддержка старого и нового формата
            for item in posted_data:
                if isinstance(item, dict) and "id" in item:
                    posted_articles[item["id"]] = {
                        "timestamp": item.get("timestamp"),
                        "message_id": item.get("message_id")
                    }
        except Exception:
            posted_articles = {}


def save_posted_articles() -> None:
    data = [
        {
            "id": id_str,
            "timestamp": info["timestamp"],
            "message_id": info.get("message_id")
        }
        for id_str, info in posted_articles.items()
    ]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_posted(article_id: str, message_id: int) -> None:
    """Сохраняем article_id + message_id для возможности удаления."""
    posted_articles[article_id] = {
        "timestamp": datetime.now().timestamp(),
        "message_id": message_id
    }
    save_posted_articles()


# ---------------- ОЧИСТКА СТАРЫХ ПОСТОВ ----------------

async def clean_old_posts() -> None:
    """
    Удаляет из канала сообщения старше RETENTION_DAYS
    и чистит локальный список.
    """
    global posted_articles
    now = datetime.now().timestamp()
    cutoff = now - (RETENTION_DAYS * 86400)
    
    to_delete = []
    to_keep = {}
    
    for article_id, info in posted_articles.items():
        ts = info.get("timestamp")
        msg_id = info.get("message_id")
        
        # Если старше 7 дней — удаляем
        if ts and ts < cutoff:
            if msg_id:
                to_delete.append((article_id, msg_id))
        else:
            to_keep[article_id] = info
    
    # Удаляем сообщения из Telegram
    deleted_count = 0
    for article_id, msg_id in to_delete:
        try:
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            deleted_count += 1
            print(f"🗑️ Удалено сообщение {msg_id}")
        except Exception as e:
            # Сообщение уже удалено или ошибка
            print(f"⚠️ Не удалось удалить {msg_id}: {e}")
        
        # Небольшая пауза чтобы не словить rate limit
        await asyncio.sleep(0.5)
    
    # Обновляем локальный список
    posted_articles = to_keep
    save_posted_articles()
    
    print(f"✅ Очистка завершена. Удалено: {deleted_count}, осталось: {len(to_keep)}")


# ---------------- HELPERS ----------------

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
        if any(kw in text for kw in REQUIRE_KEYWORDS):
            suitable.append(e)

    suitable.sort(key=lambda x: x["published_parsed"], reverse=True)
    return suitable


# ============ OPENAI TEXT ============

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    news_text = f"{title}. {summary}"
    prompt = (
        "Вот текст новости. Сделай из него короткий обзор техно-новинки для Telegram на русском:\n"
        f"{news_text}\n\n"
        "- Объём: 380–450 символов.\n"
        "- Фокус: Что именно представили, какие главные характеристики.\n"
        "- Стиль: Живой, но без 'воды'. Используй 1-2 эмодзи по теме.\n"
        "- В конце: 2-3 релевантных хештега.\n"
        "- Запрещено: Выдумывать факты.\n"
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
                    msg = await bot.send_photo(
                        CHANNEL_ID,
                        photo=FSInputFile(img),
                        caption=post_text
                    )
                    os.remove(img)
                else:
                    msg = await bot.send_message(CHANNEL_ID, text=post_text)

                # Сохраняем message_id для будущего удаления!
                save_posted(art["id"], msg.message_id)
                print(f"✅ Опубликовано! message_id: {msg.message_id}")
                break
            except Exception as e:
                print(f"❌ Ошибка отправки: {e}")
                if img and os.path.exists(img):
                    os.remove(img)


async def main():
    try:
        # 1) Сначала чистим старые посты (и из канала, и из списка)
        await clean_old_posts()
        
        # 2) Публикуем новый пост
        await autopost()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())



































































































