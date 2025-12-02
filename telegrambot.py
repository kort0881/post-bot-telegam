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
    'https://3dnews.ru/rss/news/',
    'https://habr.com/ru/feed/'
]

KEYWORDS = [
    "впн", "интернет", "технологии", "безопасность", "цензура",
    "телеграм", "ркн", "блокировка", "белые списки", "ИИ", "AI"
]

POSTED_FILE = "posted_articles.json"

# ===== Работа с уже опубликованными постами =====
if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_articles = set(json.load(f))
else:
    posted_articles = set()

def save_posted(article_id):
    posted_articles.add(article_id)
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_articles), f, ensure_ascii=False, indent=2)

# ===== Фильтрация и выбор статьи =====
def filter_article(entry):
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    return any(kw.lower() in (title.lower() + summary.lower()) for kw in KEYWORDS)

def pick_article(articles):
    sorted_a = sorted(
        articles, key=lambda e: e.get("published_parsed", datetime.now()), reverse=True
    )
    for entry in sorted_a:
        article_id = entry.get("id", entry.get("link", entry.get("title")))
        if filter_article(entry) and article_id not in posted_articles:
            return entry
    return None

# ===== Генерация текста =====
def short_summary(title, summary):
    prompt = (
        f"Сделай информативный новостной пост 4–6 предложений (~550–560 символов) по статье ниже. "
        "Добавь реализм и детализацию, не используй ссылки и хештеги.\n"
        f"Заголовок: {title}\nКратко: {summary}"
    )
    result = openai_client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{"role": "user", "content": prompt}]
    )
    content = result.choices[0].message.content.strip()
    return content[:560].rstrip()

# ===== Генерация картинки =====
def get_img_from_dalle(title):
    prompt = (
        f"Реалистичное, детализированное изображение к новости: '{title}', "
        "с акцентом на реализм, детализацию, современные технологии."
    )
    try:
        resp = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            n=1
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
    if not art:
        print("Нет новых подходящих статей")
        return

    article_id = art.get("id", art.get("link", art.get("title")))
    title = art.get("title", "")
    summary = art.get("summary", "")[:400]
    news = short_summary(title, summary)

    img_path = get_img_from_dalle(title)

    hashtags = [f"#{kw.replace(' ', '')}" for kw in KEYWORDS if kw.lower() in (title.lower() + summary.lower())]
    hashtags += ["#Новости", "#Telegram", "#Канал"]

    caption = f"{news}\n\n{' '.join(hashtags)}"

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
