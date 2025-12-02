import os
import json
import asyncio
import feedparser
from openai import OpenAI
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from datetime import datetime
import requests
from PIL import Image
from io import BytesIO

# ===== Настройки =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

RSS_FEEDS = [
    "https://3dnews.ru/rss/news/",
    "https://habr.com/ru/feed/",
]

STRONG_KEYWORDS = [
    # VPN / обход блокировок
    "vpn", "впн", "прокси", "proxy", "tor", "shadowsocks",
    "wireguard", "openvpn", "ikev2",
    "обход блокировок", "обход цензуры", "анонимность",

    # цензура и контроль интернета
    "роскомнадзор", "ркн", "суверенный интернет",
    "интернет-цензура", "цензура в интернете",
    "блокировка сайтов", "реестр запрещенных сайтов",
    "ограничение доступа", "фильтрация трафика",

    # Telegram / мессенджеры
    "телеграм", "telegram", "мессенджер", "канал в телеграме",
    "блокировка telegram",
]

SOFT_KEYWORDS = [
    # безопасность и кибератаки
    "кибербезопасность", "информационная безопасность",
    "утечка данных", "взлом", "хакеры", "ddos", "фишинг",
    "malware", "вредоносное по", "ransomware",

    # ИИ и технологии
    "искусственный интеллект", "нейросеть", "нейросети",
    "machine learning", "ml", "ai", "large language model",
    "chatgpt", "gpt", "генеративный ии",

    # законы и регулирование интернета
    "новый закон об интернете", "штраф за vpn", "ограничение интернета",
    "регулирование интернета", "экстремистский контент",
]

POSTED_FILE = "posted_articles.json"

# ===== Работа с уже опубликованными постами =====
if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_articles = set(json.load(f))
else:
    posted_articles = set()


def save_posted(article_id: str) -> None:
    posted_articles.add(article_id)
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_articles), f, ensure_ascii=False, indent=2)


# ===== Фильтрация и выбор статьи =====
def filter_article(entry):
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    text = (title + " " + summary).lower()

    if any(kw.lower() in text for kw in STRONG_KEYWORDS):
        return "strong"
    if any(kw.lower() in text for kw in SOFT_KEYWORDS):
        return "soft"
    return None


def pick_article(articles):
    scored = []
    for e in articles:
        level = filter_article(e)
        if not level:
            continue
        score = 2 if level == "strong" else 1
        scored.append((score, e))

    if scored:
        scored.sort(
            key=lambda x: (
                x[0],
                x[1].get("published_parsed", datetime.now()),
            ),
            reverse=True,
        )
        return scored[0][1]

    return None


# ===== Генерация текста =====
def short_summary(title: str, summary: str) -> str:
    prompt = (
        "Сделай информативный новостной пост 4–6 предложений "
        "(~550–560 символов) по статье ниже. "
        "Добавь реализм и детализацию, не используй ссылки и хештеги.\n"
        f"Заголовок: {title}\nКратко: {summary}"
    )
    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    content = result.choices[0].message.content.strip()
    return content[:560].rstrip()


# ===== Генерация картинки =====
def get_img_from_dalle(title: str):
    prompt = (
        f"Реалистичное, детализированное изображение к новости: '{title}', "
        "с акцентом на реализм, детализацию, современные технологии."
    )
    try:
        resp = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
        img_url = resp.data[0].url
        img = Image.open(BytesIO(requests.get(img_url).content))
        tmp = f"temp_{int(datetime.now().timestamp())}.png"
        img.save(tmp)
        return tmp
    except Exception as e:
        print("Ошибка генерации картинки:", e)
        return None


# ===== Автопостинг =====
async def autopost():
    feeds = [feedparser.parse(url) for url in RSS_FEEDS]
    articles = [entry for feed in feeds for entry in feed.entries]

    art = pick_article(articles)

    # Fallback: если по ключам ничего не нашли — берём свежую статью
    if not art and articles:
        print("Fallback: берём первую статью без фильтра")
        art = sorted(
            articles,
            key=lambda e: e.get("published_parsed", datetime.now()),
            reverse=True,
        )[0]

    if not art:
        print("Вообще нет статей в RSS")
        return

    article_id = art.get("id", art.get("link", art.get("title")))
    if article_id in posted_articles:
        print("Эта статья уже была опубликована")
        return

    title = art.get("title", "")
    summary = art.get("summary", "")[:400]
    news = short_summary(title, summary)

    # для хэштегов используем объединённый список
    all_keywords = STRONG_KEYWORDS + SOFT_KEYWORDS
    text_for_tags = (title + " " + summary).lower()
    hashtags = [
        f"#{kw.replace(' ', '')}"
        for kw in all_keywords
        if kw.lower() in text_for_tags
    ]
    hashtags += ["#Новости", "#Telegram", "#Канал"]

    caption = f"{news}\n\n{' '.join(hashtags)}"

    img_path = get_img_from_dalle(title)

    if img_path:
        with open(img_path, "rb") as ph:
            await bot.send_photo(CHANNEL_ID, ph, caption=caption)
        os.remove(img_path)
    else:
        await bot.send_message(CHANNEL_ID, caption)

    save_posted(article_id)
    print("[OK]", datetime.now(), "-", title)


# ===== Основная функция =====
async def main():
    await autopost()


if __name__ == "__main__":
    asyncio.run(main())

async def main():
    await autopost()

if __name__ == "__main__":
    asyncio.run(main())


