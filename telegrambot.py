import os
import json
import asyncio
from datetime import datetime

import requests
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from openai import OpenAI

# ===== Настройки =====
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

# ===== Ключевые слова =====
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

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_articles = set(json.load(f))
else:
    posted_articles = set()

def save_posted(article_id: str) -> None:
    posted_articles.add(article_id)
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_articles), f, ensure_ascii=False, indent=2)

def safe_get(url: str) -> str | None:
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

# ===== Парсинг =====
def load_3dnews():
    url = "https://3dnews.ru/"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    parts = html.split('<a href="/')
    for part in parts[1:6]:
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
            desc_chunk = part[desc_start:desc_start+500]
            p_start = desc_chunk.find(">")
            if p_start != -1:
                p_end = desc_chunk.find("</", p_start)
                if p_end != -1:
                    summary = clean_text(desc_chunk[p_start+1:p_end])[:300]

        articles.append({
            "id": link,
            "title": title,
            "summary": summary,
            "link": link,
            "published_parsed": datetime.now(),
        })

    print("DEBUG: статей из 3DNews:", len(articles))
    return articles

def load_habr():
    url = "https://habr.com/ru/feed/"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    chunks = html.split("<article")
    for chunk in chunks[1:6]:
        title_marker = 'data-test-id="article-title-link"'
        idx = chunk.find(title_marker)
        if idx == -1:
            continue
        sub = chunk[idx:]
        href_pos = sub.find('href="')
        if href_pos == -1:
            continue
        href_start = href_pos + len('href="')
        href_end = sub.find('"', href_start)
        href = sub[href_start:href_end]

        title_start = sub.find(">", href_end) + 1
        title_end = sub.find("</a>", title_start)
        title = clean_text(sub[title_start:title_end])

        link = "https://habr.com" + href

        p_start = chunk.find("<p")
        if p_start != -1:
            p_start = chunk.find(">", p_start) + 1
            p_end = chunk.find("</p>", p_start)
            summary = clean_text(chunk[p_start:p_end])[:300]
        else:
            summary = ""

        articles.append({
            "id": link,
            "title": title,
            "summary": summary,
            "link": link,
            "published_parsed": datetime.now(),
        })

    print("DEBUG: статей из Хабра:", len(articles))
    return articles

def load_tproger():
    url = "https://tproger.ru/news"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    parts = html.split('<a ')
    for part in parts[1:6]:
        if "href=" not in part or "news" not in part:
            continue

        href_pos = part.find('href="')
        href_start = href_pos + len('href="')
        href_end = part.find('"', href_start)
        href = part[href_start:href_end]

        title_start = part.find(">", href_end) + 1
        title_end = part.find("</a>", title_start)
        title = clean_text(part[title_start:title_end])
        if not title:
            continue

        if href.startswith("http"):
            link = href
        else:
            link = "https://tproger.ru" + href

        summary = ""

        articles.append({
            "id": link,
            "title": title,
            "summary": summary,
            "link": link,
            "published_parsed": datetime.now(),
        })

    print("DEBUG: статей из Tproger:", len(articles))
    return articles

def load_articles_from_sites():
    articles = []
    articles.extend(load_3dnews())
    articles.extend(load_habr())
    articles.extend(load_tproger())
    print("DEBUG: всего статей:", len(articles))
    return articles

def filter_article(entry):
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
            key=lambda x: (x[0], x[1].get("published_parsed", datetime.now())),
            reverse=True,
        )
        return scored[0][1]
    return None

# ===== Генерация текста =====
def short_summary(title: str, summary: str) -> str:
    news_text = f"{title}. {summary}" if summary else title
    
    prompt = (
        f"Перепиши эту техническую новость в стиле Telegram-канала:\n\n"
        f"{news_text}\n\n"
        f"ПРАВИЛА:\n"
        f"1. Пиши КОНКРЕТНО — если упомянуто название компании/продукта, используй его! НЕ пиши 'Компания X' или 'Продукт Y'.\n"
        f"2. Если есть версии, цифры, проценты — обязательно указывай.\n"
        f"3. Пиши простым языком, без канцелярита.\n"
        f"4. Объём: 650-700 символов (без PS).\n"
        f"5. Формат:\n"
        f"   [эмодзи] Строка 1: что произошло\n"
        f"   [эмодзи] Строка 2: какая была проблема\n"
        f"   [эмодзи] Строка 3: что улучшилось\n"
        f"   [эмодзи] Строка 4: зачем это нужно\n"
        f"   \n"
        f"   PS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6\n\n"
        f"Верни только текст поста!"
    )
    
    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return result.choices[0].message.content.strip()[:850]

# ===== Генерация промпта для картинки =====
def generate_image_prompt(title: str, summary: str) -> str:
    prompt = (
        f"Create a short image prompt for: {title}. "
        f"Style: cinematic realistic, dramatic lighting, dark tech atmosphere, high detail. "
        f"Focus on technology/cybersecurity/internet themes. No text, no logos. Max 200 chars."
    )
    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return result.choices[0].message.content.strip()[:200]

# ===== POLLINATIONS.AI - Бесплатная генерация картинок =====
def generate_image_pollinations(prompt: str) -> str | None:
    try:
        print(f"Генерация Pollinations: {prompt}")
        
        # URL автоматически генерирует картинку
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        params = {
            "width": "1024",
            "height": "1024",
            "nologo": "true",
            "model": "flux"
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

# ===== Автопостинг с картинками =====
async def autopost():
    articles = load_articles_from_sites()

    if not articles:
        print("Нет статей")
        return

    art = pick_article(articles)

    if not art:
        print("Нет подходящих статей")
        return

    article_id = art.get("id", art.get("link"))
    if article_id in posted_articles:
        print("Уже была опубликована")
        return

    title = art.get("title", "")
    summary = art.get("summary", "")[:400]

    print(f"\n{'='*60}")
    print(f"ВЫБРАНА: {title}")
    print(f"ОПИСАНИЕ: {summary}")
    print(f"ССЫЛКА: {art.get('link')}")
    print(f"{'='*60}\n")

    news = short_summary(title, summary)
    print(f"ТЕКСТ ({len(news)} симв.):\n{news}\n{'='*60}\n")

    # Генерация картинки через Pollinations
    image_prompt = generate_image_prompt(title, summary)
    image_file = generate_image_pollinations(image_prompt)

    all_keywords = STRONG_KEYWORDS + SOFT_KEYWORDS
    text_for_tags = (title + " " + summary).lower()
    hashtags = [f"#{kw.replace(' ', '')}" for kw in all_keywords if kw.lower() in text_for_tags]
    hashtags += ["#Новости", "#Telegram", "#Канал"]

    caption = f"{news}\n\n{' '.join(hashtags)}"

    # Отправка
    if image_file and os.path.exists(image_file):
        photo = FSInputFile(image_file)
        await bot.send_photo(CHANNEL_ID, photo=photo, caption=caption)
        os.remove(image_file)
        print("✅ Пост с картинкой отправлен!")
    else:
        await bot.send_message(CHANNEL_ID, caption)
        print("⚠️ Пост без картинки")

    save_posted(article_id)
    print(f"[OK] {datetime.now()}")

async def main():
    await autopost()

if __name__ == "__main__":
    asyncio.run(main())























