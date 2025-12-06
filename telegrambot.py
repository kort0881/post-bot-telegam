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

# ---------------- CONFIG ----------------

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

POSTED_FILE = "posted_articles.json"
RETENTION_DAYS = 7

# ---------------- KEYWORDS ----------------

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
    "роскомсвобода", "блокировка", "отключение",
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

# ---------------- STATE ----------------

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

def save_posted_articles() -> None:
    data = [{"id": id_str, "timestamp": ts} for id_str, ts in posted_articles.items()]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
            print(f"HTTP {resp.status_code} для {url}")
            return None
        return resp.text
    except Exception as e:
        print(f"Ошибка запроса {url}: {e}")
        return None

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())

# ---------------- PARSERS ----------------

def load_3dnews() -> List[Dict]:
    url = "https://3dnews.ru/"
    html = safe_get(url)
    if not html:
        return []

    articles = []
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

    print(f"DEBUG: 3DNews – {len(articles)} статей")
    return articles
# ---------------- RSS PARSERS ----------------

def load_rss(url: str, source: str) -> List[Dict]:
    print(f"Загружаем RSS: {url}")
    articles = []

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"Ошибка RSS {url}: {e}")
        return articles

    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:400]
        if not link or not title:
            continue

        published_parsed = datetime.now()
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published_parsed = datetime(*entry.published_parsed[:6])
            except:
                pass

        articles.append({
            "id": link,
            "title": title,
            "summary": summary,
            "link": link,
            "source": source,
            "published_parsed": published_parsed,
        })

    print(f"DEBUG: {source} – {len(articles)} статей")
    return articles

def load_articles_from_sites() -> List[Dict]:
    articles = []
    articles.extend(load_3dnews())
    articles.extend(load_rss("https://vc.ru/rss", "VC.ru/rss"))
    articles.extend(load_rss("https://habr.com/ru/rss/all/all/?fl=ru", "Habr/rss"))
    articles.extend(load_rss("https://xakep.ru/feed/", "Xakep.ru/rss"))
    print(f"ВСЕГО СПАРСЕНО: {len(articles)} статей")
    return articles

# ---------------- FILTER ----------------

def filter_article(entry: Dict) -> Optional[str]:
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    text = (title + " " + summary).lower()

    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return None

    if any(kw in text for kw in STRONG_KEYWORDS):
        return "strong"
    if any(kw in text for kw in SOFT_KEYWORDS):
        return "soft"

    return None

# ---------------- PICK ARTICLE ----------------

PRIORITY_CHANNEL_KEYWORDS = [
    "vpn", "впн", "proxy", "прокси", "роскомнадзор", "ркн",
    "блокировка сайтов", "блокировка", "отключение",
    "обход блокировок", "обход цензуры", "цензура", "суверенный интернет",
    "белые списки", "минцифры", "роскомсвобода"
]

def pick_article(articles: List[Dict]) -> Optional[Dict]:
    strong_soft = []
    fallback = []
    third_stage = []
    skipped = 0

    for e in articles:
        aid = e.get("id")
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = (title + " " + summary).lower()

        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

        level = filter_article(e)
        if level:
            score = 2 if level == "strong" else 1
            strong_soft.append((score, e))
        elif any(kw in text for kw in PRIORITY_CHANNEL_KEYWORDS):
            third_stage.append(e)
        else:
            fallback.append(e)

    print(f"Пропущено опубликованных: {skipped}")
    print(f"По ключам найдено: {len(strong_soft)}, запасных: {len(fallback)}, третий этап: {len(third_stage)}")

    if strong_soft:
        strong_soft.sort(key=lambda x: (x[0], x[1].get("published_parsed", datetime.now())), reverse=True)
        return strong_soft[0][1]
    if fallback:
        fallback.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        return fallback[0]
    if third_stage:
        third_stage.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        return third_stage[0]

    return None

# ---------------- OPENAI ----------------

def short_summary(title: str, summary: str) -> str:
    news_text = f"{title}. {summary}" if summary else title
    prompt = (
        f"Перепиши новость в стиле Telegram-канала:\n\n"
        f"{news_text}\n\n"
        f"ПРАВИЛА:\n"
        f"1. Используй реальные названия.\n"
        f"2. Если есть цифры — оставляй.\n"
        f"3. Объем: 400–600 символов.\n"
        f"4. Формат:\n"
        f"   [эмоджи] Что произошло\n"
        f"   [эмоджи] Какая была проблема\n"
        f"   [эмоджи] Что улучшилось\n"
        f"   [эмоджи] Зачем это нужно\n\n"
        f"В конце НИЧЕГО не добавляй после основного текста."
    )
    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    text = res.choices[0].message.content.strip()
    ps = "PS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
    return text + "\n\n" + ps

def generate_image_prompt(title: str, summary: str) -> str:
    base_prompt = f"Create cinematic, realistic image about: {title}. Dark tech atmosphere. No text. Max 200 chars."
    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": base_prompt}],
    )
    return res.choices[0].message.content.strip()[:200]

def generate_image_pollinations(prompt: str) -> Optional[str]:
    try:
        print("Генерирую картинку Pollinations...")
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        params = {"width": "1024", "height": "1024", "nologo": "true", "model": "flux"}
        r = requests.get(url, params=params, timeout=60)
        if r.status_code != 200:
            print("Ошибка Pollinations:", r.status_code)
            return None
        filename = f"news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        with open(filename, "wb") as f:
            f.write(r.content)
        return filename
    except Exception as e:
        print("Ошибка картинки:", e)
        return None

# ---------------- AUTPOST ----------------

async def autopost():
    clean_old_posts()
    articles = load_articles_from_sites()
    if not articles:
        print("Нет статей")
        return

    art = pick_article(articles)
    if not art:
        print("Нет подходящих статей")
        return

    aid = art["id"]
    if aid in posted_articles:
        print("Статья уже в posted_articles, выходим")
        return
    posted_articles[aid] = datetime.now().timestamp()
    save_posted_articles()

    print("\nВыбрана статья:", art["title"], "\n")

    try:
        text = short_summary(art["title"], art.get("summary", ""))
        img_prompt = generate_image_prompt(art["title"], art.get("summary", ""))
        img_file = generate_image_pollinations(img_prompt)

        if img_file and os.path.exists(img_file):
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=FSInputFile(img_file),
                caption=text,
                parse_mode=ParseMode.HTML,
            )
            os.remove(img_file)
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode=ParseMode.HTML,
            )

        print("Статья успешно отправлена.")
    except Exception as e:
        print("Ошибка Telegram:", e)

if __name__ == "__main__":
    asyncio.run(autopost())




















































