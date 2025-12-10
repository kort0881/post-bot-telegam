import os
import json
import asyncio
import time
import random
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

# ---------------- KEYWORDS ----------------

STRONG_KEYWORDS = [
    "vpn", "впн", "прокси", "proxy", "tor", "shadowsocks",
    "wireguard", "openvpn", "роскомнадзор", "ркн",
    "блокировка сайтов", "блокировка", "блокиров",
    "обход блокировок", "обход цензуры", "цензур",
    "telegram", "телеграм", "whatsapp", "signal",
    "dpi", "минцифры", "суверенный интернет",
    "белые списки", "роскомсвобода", "запрещенн",
]

SOFT_KEYWORDS = [
    "кибербезопасность", "киберзащита", "информационная безопасность",
    "конфиденциальность", "privacy", "анонимность",
    "шифрование", "encryption", "безопасность данных",
    "утечка данных", "взлом", "хакер", "malware", "вирус",
    "уязвимость", "vulnerability", "эксплойт",
    "искусственный интеллект", "нейросет", "машинное обучение",
    "chatgpt", "claude", "gemini", "llm",
]

EXCLUDE_KEYWORDS = [
    "теннис", "футбол", "хоккей", "баскетбол", "волейбол", "спорт",
    "олимпиад", "соревнован", "чемпионат", "турнир",
    "игра", "геймплей", "gameplay", "dungeon", "quest",
    "playstation", "xbox", "nintendo", "steam", "boss", "raid",
    "шутер", "mmorpg", "battle royale", "геймер", "gamer",
    "helldivers", "routine", "игровой", "игровых",
    "моя жизнь", "мой опыт", "как я", "моя история",
    "вернулся", "вернулась", "личный опыт",
    "кино", "фильм", "сериал", "музыка", "концерт",
    "дайджест", "digest", "обзор игр", "новости игр",
]

# ---------------- STATE ----------------

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            if isinstance(posted_data, list) and posted_data and isinstance(posted_data[0], dict):
                posted_articles = {item["id"]: item.get("timestamp") for item in posted_data}
            else:
                posted_articles = {id_str: None for id_str in posted_data}
        except Exception as e:
            print(f"⚠️ Ошибка загрузки: {e}")
            posted_articles = {}
else:
    posted_articles = {}

def save_posted_articles() -> None:
    try:
        data = [{"id": id_str, "timestamp": ts} for id_str, ts in posted_articles.items()]
        with open(POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения: {e}")

def clean_old_posts() -> None:
    global posted_articles
    now = datetime.now().timestamp()
    cutoff = now - (RETENTION_DAYS * 86400)
    old_count = len(posted_articles)
    posted_articles = {
        id_str: ts for id_str, ts in posted_articles.items()
        if ts is None or ts > cutoff
    }
    removed = old_count - len(posted_articles)
    if removed > 0:
        print(f"🗑️ Удалено старых постов: {removed}")
    save_posted_articles()

def save_posted(article_id: str) -> None:
    posted_articles[article_id] = datetime.now().timestamp()
    save_posted_articles()

# ---------------- HELPERS ----------------

def safe_get(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception as e:
        print(f"Ошибка запроса {url}: {e}")
        return None

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())

# ---------------- PARSERS ----------------

def load_3dnews() -> List[Dict]:
    try:
        html = safe_get("https://3dnews.ru/")
        if not html:
            return []

        articles = []
        parts = html.split('<a href="/')

        for part in parts[1:15]:
            try:
                href_end = part.find('"')
                title_start = part.find(">")
                title_end = part.find("</a>")
                if href_end == -1 or title_start == -1 or title_end == -1:
                    continue

                href = part[:href_end]
                title = clean_text(part[title_start + 1:title_end])
                if not title:
                    continue

                link = "https://3dnews.ru/" + href.lstrip("/")
                summary = ""

                desc_start = part.find('class="')
                if desc_start != -1:
                    desc_chunk = part[desc_start:desc_start + 500]
                    p_start = desc_chunk.find(">")
                    if p_start != -1:
                        p_end = desc_chunk.find("</", p_start)
                        if p_end != -1:
                            summary = clean_text(desc_chunk[p_start + 1:p_end])[:300]

                articles.append({
                    "id": link,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "source": "3DNews",
                    "published_parsed": datetime.now(),
                })
            except Exception:
                continue

        return articles
    except Exception as e:
        print(f"Ошибка 3DNews: {e}")
        return []

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:50]:
            try:
                link = entry.get("link", "")
                title = clean_text(entry.get("title") or "")
                summary = clean_text(entry.get("summary") or entry.get("description") or "")[:400]
                if not link or not title:
                    continue

                published_parsed = datetime.now()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published_parsed = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass

                articles.append({
                    "id": link,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "source": source,
                    "published_parsed": published_parsed,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"Ошибка RSS {url}: {e}")
    return articles

def load_articles_from_sites() -> List[Dict]:
    articles = []
    articles.extend(load_3dnews())
    articles.extend(load_rss("https://vc.ru/rss", "VC.ru"))
    articles.extend(load_rss("https://xakep.ru/feed/", "Xakep.ru"))
    articles.extend(load_rss("https://xakep.ru/tag/iskusstvennyj-intellekt/feed/", "Xakep.ru/AI"))
    print(f"ВСЕГО: {len(articles)} статей")
    return articles

# ---------------- FILTER ----------------

def check_keywords(text: str) -> Optional[str]:
    text_lower = text.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text_lower:
            return None
    if any(kw in text_lower for kw in STRONG_KEYWORDS):
        return "strong"
    if any(kw in text_lower for kw in SOFT_KEYWORDS):
        return "soft"
    return None

# ---------------- PICK ARTICLE ----------------

def pick_article(articles: List[Dict]) -> Optional[Dict]:
    filtered_strong = []
    filtered_soft = []
    ai_articles = []
    skipped = 0
    excluded = 0

    for e in articles:
        aid = e.get("id")
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = title + " " + summary
        source = e.get("source", "")
        text_lower = text.lower()

        if any(kw in text_lower for kw in EXCLUDE_KEYWORDS):
            excluded += 1
            continue

        level = check_keywords(text)
        if level == "strong":
            filtered_strong.append(e)
        elif level == "soft":
            filtered_soft.append(e)
        elif source == "Xakep.ru/AI":
            ai_articles.append(e)

    print(f"Пропущено: {skipped}, Исключено: {excluded}")
    print(f"Сильные: {len(filtered_strong)}, Слабые: {len(filtered_soft)}, AI: {len(ai_articles)}")

    if filtered_strong:
        filtered_strong.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("✅ По СИЛЬНЫМ ключам")
        return filtered_strong[0]

    if filtered_soft:
        filtered_soft.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("✅ По СЛАБЫМ ключам")
        return filtered_soft[0]

    if ai_articles:
        ai_articles.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("⚠️ Из Xakep.ru/AI")
        return ai_articles[0]

    return None

# ---------------- OPENAI TEXT (650–700) ----------------

def short_summary(title: str, summary: str) -> str:
    """Пост 650–700 символов с завершённой мыслью"""
    news_text = f"{title}. {summary}" if summary else title
    prompt = (
        "Сделай новостной пост для Telegram по теме ниже.\n\n"
        f"{news_text}\n\n"
        "Требования:\n"
        "- Объём: строго 650–700 символов.\n"
        "- Мысль должна быть законченной, без обрыва.\n"
        "- Стиль: технологичный, нейтральный, чуть жёсткий.\n"
        "- Кратко объясни: что произошло, кому это важно и к чему может привести.\n"
        "- В конце 2–4 релевантных хештега через пробел.\n"
        "- 2–3 эмодзи по смыслу внутри текста."
    )

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=450,
        )
        text = res.choices[0].message.content.strip()

        if len(text) > 700:
            print(f"⚠️ Текст {len(text)} символов, режу до 700")
            text = text[:697] + "…"
        elif len(text) < 650:
            print(f"⚠️ Текст всего {len(text)} символов")

        ps = "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        full_text = text + ps

        if len(full_text) > 1020:
            excess = len(full_text) - 1020
            text = text[:-(excess + 3)] + "…"
            full_text = text + ps

        print(f"📊 Итоговая длина: {len(full_text)} символов")
        return full_text

    except Exception as e:
        print(f"❌ OpenAI: {e}")
        fallback = f"{title}\n\n{(summary or '')[:660]}"
        return f"{fallback} 🔐🌐\n\n#tech #новости\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"

# ---------------- IMAGE GENERATION (DeepAI REALISTIC) ----------------

def generate_image(title: str) -> Optional[str]:
    """
    Реалистичное кинематографичное изображение без киберпанка и неона.
    Используем DeepAI text2img как новый бесплатный генератор.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    base_prompt = (
        f"realistic cinematic detailed photo about {title[:60]}, "
        "modern cybersecurity and technology, professional corporate style, "
        "clean composition, neutral background, sharp focus, high detail, 4k. "
        "No cyberpunk, no neon, no sci-fi, no holograms, no glowing effects, no dystopia."
    )

    print("🎨 Генерация через DeepAI")
    print(f"   Промпт: {base_prompt[:140]}...")

    try:
        url = "https://api.deepai.org/api/text2img"
        data = {"text": base_prompt}

        resp = requests.post(url, data=data, timeout=90)
        if resp.status_code != 200:
            print(f"❌ DeepAI HTTP {resp.status_code}")
            return None

        result = resp.json()
        img_url = result.get("output_url")
        if not img_url:
            print("❌ DeepAI не вернул output_url")
            return None

        img_resp = requests.get(img_url, timeout=60)
        if img_resp.status_code != 200:
            print(f"❌ Не удалось скачать картинку DeepAI: HTTP {img_resp.status_code}")
            return None

        filename = f"news_{timestamp}_{random.randint(1000,9999)}.jpg"
        with open(filename, "wb") as f:
            f.write(img_resp.content)

        print(f"✅ Картинка сохранена: {filename}")
        return filename

    except requests.exceptions.Timeout:
        print("⏱️ Timeout DeepAI")
        return None
    except Exception as e:
        print(f"❌ Ошибка DeepAI: {e}")
        return None

# ---------------- AUTOPOST ----------------

async def autopost():
    clean_old_posts()
    articles = load_articles_from_sites()
    if not articles:
        print("Нет статей")
        return

    art = pick_article(articles)
    if not art:
        print("Нет подходящих")
        return

    aid = art["id"]
    print(f"\n✅ Выбрана: {art['title']}")
    print(f"Источник: {art['source']}\n")

    try:
        text = short_summary(art["title"], art.get("summary", ""))
        img_file = generate_image(art["title"])

        if img_file and os.path.exists(img_file):
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=FSInputFile(img_file),
                caption=text,
            )
            os.remove(img_file)
            print("✅ Отправлено с картинкой")
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
            )
            print("✅ Отправлено без картинки")

        save_posted(aid)

    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

if __name__ == "__main__":
    asyncio.run(autopost())












































































