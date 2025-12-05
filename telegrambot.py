import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
import feedparser
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

print("DEBUG OPENAI KEY LEN:", len(OPENAI_API_KEY) if OPENAI_API_KEY else 0)

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

STRONG_KEYWORDS = [
    "vpn", "впн", "прокси", "proxy", "tor", "shadowsocks",
    "wireguard", "openvpn", "ikev2",
    "обход блокировок", "обход цензуры", "анонимность",
    "роскомнадзор", "ркн",
    "интернет-цензура", "цензура в интернете",
    "блокировка сайтов", "блокировка ресурса",
    "реестр запрещенных сайтов", "ограничение доступа",
    "фильтрация трафика", "dpi", "deep packet inspection",
    "телеграм", "telegram",
    "whatsapp", "signal", "viber",
    "messenger", "мессенджер",
    "обновление безопасности", "патч безопасности",
    "антивирус", "firewall", "фаервол",
    "браузер", "браузер tor",
    "клиент vpn", "vpn-клиент",
    "минцифры", "минцифры рф",
    "белые списки", "белый список",
]

SOFT_KEYWORDS = [
    "приложение для пк", "desktop-приложение", "утилита для windows",
    "программа для macos", "open source",
    "искусственный интеллект", "нейросеть", "нейросети",
    "ai", "machine learning",
    "кибербезопасность", "информационная безопасность",
    "конфиденциальность в интернете", "privacy",
    "суверенный интернет", "ограничение интернета",
]

EXCLUDE_KEYWORDS = [
    "игра", "игры", "game", "games", "геймплей", "gameplay",
    "dungeon", "quest", "босс", "boss", "рейд", "raid",
    "онлайн-игра", "игровой", "гейминг", "gaming",
    "playstation", "xbox", "nintendo", "steam",
    "шутер", "rpg", "mmorpg", "moba", "battle royale",
]

POSTED_FILE = "posted_articles.json"
RETENTION_DAYS = 7

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            if isinstance(posted_data, list) and posted_data and isinstance(posted_data[0], dict):
                posted_articles = {item["id"]: item.get("timestamp") for item in posted_data}
            else:
                posted_articles = {id_str: None for id_str in posted_data}
        except Exception:
            posted_articles = {}
else:
    posted_articles = {}


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


def save_posted_articles() -> None:
    data = [
        {"id": id_str, "timestamp": ts}
        for id_str, ts in posted_articles.items()
    ]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_posted(article_id: str) -> None:
    posted_articles[article_id] = datetime.now().timestamp()
    save_posted_articles()


def safe_get(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code} для {url}")
            return None
        return resp.text
    except Exception as e:
        print(f"Ошибка при запросе {url}:", e)
        return None


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


def load_3dnews() -> List[Dict]:
    url = "https://3dnews.ru/"
    html = safe_get(url)
    if not html:
        return []

    articles: List[Dict] = []
    parts = html.split('<a href="/')

    for part in parts[1:4]:
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

    print(f"DEBUG: 3DNews - {len(articles)} статей")
    return articles


VC_RU_FEED = "https://vc.ru/rss"


def load_vcru_from_rss() -> List[Dict]:
    articles: List[Dict] = []

    print(f"Загружаем RSS VC.ru: {VC_RU_FEED}")
    try:
        feed = feedparser.parse(VC_RU_FEED)
    except Exception as e:
        print(f"Ошибка RSS {VC_RU_FEED}: {e}")
        return articles

    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        title = clean_text(entry.get("title", "") or "")
        summary = clean_text(
            entry.get("summary", "") or entry.get("description", "") or ""
        )[:400]

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
            "source": "VC.ru/rss",
            "published_parsed": published_parsed,
        })

    print(f"DEBUG: VC.ru RSS - {len(articles)} статей")
    return articles


def load_articles_from_sites() -> List[Dict]:
    articles: List[Dict] = []
    articles.extend(load_3dnews())
    articles.extend(load_vcru_from_rss())

    print(f"\n{'=' * 60}")
    print(f"ВСЕГО СПАРСЕНО: {len(articles)} статей")
    print(f"В памяти опубликовано: {len(posted_articles)} статей")
    print(f"{'=' * 60}")
    for i, art in enumerate(articles, 1):
        print(f"{i}. [{art['source']}] {art['title'][:80]}")
    print(f"{'=' * 60}\n")

    return articles


def filter_article(entry: Dict) -> Optional[str]:
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    text = (title + " " + summary).lower()

    if any(kw.lower() in text for kw in EXCLUDE_KEYWORDS):
        return None

    if any(kw.lower() in text for kw in STRONG_KEYWORDS):
        return "strong"
    if any(kw.lower() in text for kw in SOFT_KEYWORDS):
        return "soft"
    return None


def pick_article(articles: List[Dict]) -> Optional[Dict]:
    scored = []
    skipped_count = 0
    
    for e in articles:
        article_id = e.get("id", e.get("link"))
        if article_id in posted_articles:
            skipped_count += 1
            continue

        level = filter_article(e)
        if not level:
            continue

        score = 2 if level == "strong" else 1
        scored.append((score, e))

    print(f"ПРОПУЩЕНО ОПУБЛИКОВАННЫХ: {skipped_count}")
    print(f"ПОДХОДЯЩИХ НОВЫХ СТАТЕЙ: {len(scored)}")
    for i, (score, art) in enumerate(scored[:5], 1):
        level = "STRONG" if score == 2 else "SOFT"
        print(f"{i}. [{level}] [{art['source']}] {art['title'][:80]}")
    print(f"{'=' * 60}\n")

    if scored:
        scored.sort(
            key=lambda x: (
                x[0],
                x[1].get("published_parsed", datetime.now())
            ),
            reverse=True,
        )
        return scored[0][1]

    return None


def short_summary(title: str, summary: str) -> str:
    news_text = f"{title}. {summary}" if summary else title

    prompt = (
        f"Перепиши эту техническую новость в стиле Telegram-канала:\n\n"
        f"{news_text}\n\n"
        f"ПРАВИЛА:\n"
        f"1. Пиши КОНКРЕТНО — если упомянуто название компании/продукта, используй его! НЕ пиши 'Компания X' или 'Продукт Y'.\n"
        f"2. Если есть версии, цифры, проценты — обязательно указывай.\n"
        f"3. Пиши простым языком, без канцелярита.\n"
        f"4. Объём основного текста: примерно 400–600 символов.\n"
        f"5. Формат строго такой:\n"
        f"   [эмоджи] Строка 1: что произошло\n"
        f"   [эмоджи] Строка 2: какая была проблема\n"
        f"   [эмоджи] Строка 3: что улучшилось\n"
        f"   [эмоджи] Строка 4: зачем это нужно\n"
        f"   ПУСТАЯ СТРОКА\n"
        f"   PS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6\n\n"
        f"Верни только готовый текст поста целиком, включая строку PS."
    )

    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    text = result.choices[0].message.content.strip()

    ps = "PS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
    if ps not in text:
        text = f"{text.rstrip()}\n\n{ps}"

    return text


def generate_image_prompt(title: str, summary: str) -> str:
    base_prompt = (
        f"Create a short image prompt for: {title}. "
        f"Style: cinematic realistic, dramatic lighting, dark tech atmosphere, high detail. "
        f"Focus on technology/cybersecurity/internet themes. No text, no logos. Max 200 chars."
    )
    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": base_prompt}],
    )
    return result.choices[0].message.content.strip()[:200]


def generate_image_pollinations(prompt: str) -> Optional[str]:
    try:
        print(f"Генерация Pollinations: {prompt}")

        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        params = {
            "width": "1024",
            "height": "1024",
            "nologo": "true",
            "model": "flux",
        }

        response = requests.get(url, params=params, timeout=60)

        if response.status_code == 200:
            filename = f"news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            with open(filename, "wb") as f:
                f.write(response.content)
            print(f"✅ Картинка: {filename}")
            return filename
        else:
            print(f"❌ Ошибка Pollinations: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Ошибка генерации: {e}")
        return None


async def autopost():








































