import os
import json
import asyncio
import random
import time
from datetime import datetime
from typing import List, Dict, Optional

import requests
import feedparser

from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from openai import OpenAI

# ---------------- CONFIG ----------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ARTICLES_FILE = "articles_log.json"
MAX_ARTICLES = 500

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------------------
# Загрузка и сохранение логов
# ------------------------------------------
def load_articles() -> Dict:
    if not os.path.exists(ARTICLES_FILE):
        return {"articles": [], "timestamps": {}}
    try:
        with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"articles": [], "timestamps": {}}

def save_articles(db: Dict):
    try:
        with open(ARTICLES_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except:
        pass

def clean_old_articles(db: Dict):
    articles = db.get("articles", [])
    if len(articles) > MAX_ARTICLES:
        db["articles"] = articles[-MAX_ARTICLES:]

# ------------------------------------------
# RSS парсер
# ------------------------------------------
def fetch_rss(feed_urls: List[str]) -> List[Dict]:
    items = []
    for url in feed_urls:
        try:
            data = feedparser.parse(url)
            for entry in data.entries:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")
                })
        except:
            continue
    return items

# ------------------------------------------
# Генерация корпоративного фото
# ------------------------------------------
def generate_image(title: str) -> Optional[str]:
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    style = "realistic corporate photo, cinematic lighting, professional, high clarity, neutral tech aesthetic, clean, detailed, sharp"

    prompt = (
        f"{style}. Related to '{title[:60]}'. "
        "No cyberpunk, no neon, no futuristic, no sci-fi, no holograms, no dystopia."
    )

    services = [
        ("Flux-Realism", "flux-realism", 90),
        ("Flux", "flux", 75),
        ("Turbo", "turbo", 45)
    ]

    with requests.Session() as session:
        for name, model, timeout in services:
            try:
                seed = str(int(time.time() * 1000) + random.randint(1000, 9999))
                print(f"🎨 {name} (seed: {seed})")
                print(f"   Промпт: {prompt[:120]}...")

                url = "https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt)
                params = {
                    "width": "1024",
                    "height": "1024",
                    "nologo": "true",
                    "model": model,
                    "seed": seed,
                }

                r = session.get(url, params=params, timeout=timeout)
                if r.status_code == 200:
                    path = f"generated_{timestamp}.jpg"
                    with open(path, "wb") as f:
                        f.write(r.content)
                    return path
            except:
                continue
    return None

# ------------------------------------------
# Генерация текста через OpenAI (700-800 символов)
# ------------------------------------------
def ai_generate_text(title: str, summary: str) -> str:
    prompt = (
        "Сделай короткий новостной текст (700–800 символов) по теме:\n"
        f"Заголовок: {title}\n"
        f"Описание: {summary}\n"
        "Стиль: нейтральный, информационный, технологичный, чуть жестче."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except:
        return f"{title}\n\n{summary[:750]}"

# ------------------------------------------
# Отправка в Telegram
# ------------------------------------------
async def send_message(text: str, image_path: Optional[str]):
    if image_path and os.path.exists(image_path):
        await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=FSInputFile(image_path), caption=text)
    else:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)

# ------------------------------------------
# Основной цикл
# ------------------------------------------
async def main_loop():
    FEEDS = [
        "https://xakep.ru/feed/",
        "https://3dnews.ru/software-news/rss",
        "https://www.securitylab.ru/_services/export/rss/",
    ]

    db = load_articles()

    while True:
        try:
            print("\n=== Обновление RSS ===")
            items = fetch_rss(FEEDS)

            strong, weak, ai = [], [], []

            for item in items:
                title = item["title"]
                if title in db["articles"]:
                    continue
                db["articles"].append(title)
                clean_old_articles(db)
                save_articles(db)

                # Классификация
                t_lower = title.lower()
                if "уязв" in t_lower or "атака" in t_lower:
                    strong.append(item)
                elif "обновл" in t_lower:
                    weak.append(item)
                else:
                    ai.append(item)

            print(f"ВСЕГО: {len(items)} статей")
            print(f"Сильные: {len(strong)}, Слабые: {len(weak)}, AI: {len(ai)}")

            target = strong[0] if strong else weak[0] if weak else ai[0] if ai else None
            if not target:
                await asyncio.sleep(120)
                continue

            title = target["title"]
            summary = target["summary"]

            print(f"▶ Обрабатываю: {title}")
            text = ai_generate_text(title, summary)
            img = generate_image(title)
            if img:
                print("Картинка создана.")
            else:
                print("❌ Все сервисы недоступны — отправляю без картинки")

            await send_message(text, img)

        except Exception as e:
            print("ОШИБКА:", e)

        await asyncio.sleep(120)

# ------------------------------------------
# START
# ------------------------------------------
if __name__ == "__main__":
    asyncio.run(main_loop())







































































