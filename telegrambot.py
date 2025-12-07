import os
import json
import asyncio
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
]

# СТРОГИЕ ИСКЛЮЧЕНИЯ - убираем такие статьи
EXCLUDE_KEYWORDS = [
    # Спорт и игры
    "теннис", "футбол", "хоккей", "баскетбол", "волейбол", "спорт",
    "игра", "геймплей", "gameplay", "dungeon", "quest",
    "playstation", "xbox", "nintendo", "steam", "boss", "raid",
    "шутер", "mmorpg", "battle royale", "геймер",
    # Личные истории и блоги
    "моя жизнь", "мой опыт", "как я", "моя история",
    "вернулся", "вернулась", "личный опыт",
    # Развлечения
    "кино", "фильм", "сериал", "музыка", "концерт",
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
            except Exception as e:
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
            except Exception as e:
                continue

    except Exception as e:
        print(f"Ошибка RSS {url}: {e}")

    return articles

def load_articles_from_sites() -> List[Dict]:
    articles = []
    articles.extend(load_3dnews())
    articles.extend(load_rss("https://vc.ru/rss", "VC.ru"))
    articles.extend(load_rss("https://habr.com/ru/rss/all/all/?fl=ru", "Habr"))
    articles.extend(load_rss("https://xakep.ru/feed/", "Xakep.ru"))
    print(f"ВСЕГО: {len(articles)} статей")
    return articles

# ---------------- FILTER (УСИЛЕННЫЙ) ----------------

def check_keywords(text: str) -> Optional[str]:
    """Строгая проверка по ключевым словам"""
    text_lower = text.lower()
    
    # СНАЧАЛА проверяем исключения (если хоть одно слово - отклоняем)
    for kw in EXCLUDE_KEYWORDS:
        if kw in text_lower:
            print(f"❌ Отклонено по слову: '{kw}'")
            return None
    
    # Проверяем сильные ключевые слова
    if any(kw in text_lower for kw in STRONG_KEYWORDS):
        return "strong"
    
    # Проверяем слабые ключевые слова
    if any(kw in text_lower for kw in SOFT_KEYWORDS):
        return "soft"
    
    return None

# ---------------- PICK ARTICLE ----------------

def pick_article(articles: List[Dict]) -> Optional[Dict]:
    """
    ЛОГИКА:
    1. Сначала ищем по ключевым словам (strong/soft)
    2. Если НЕ нашли - берём самую свежую ИЗ ТЕХНИЧЕСКИХ источников
    3. НО только если она прошла проверку на исключения
    """
    filtered_strong = []
    filtered_soft = []
    all_fresh = []
    skipped = 0
    excluded = 0

    for e in articles:
        aid = e.get("id")
        
        # Пропускаем уже опубликованные
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = title + " " + summary

        # Проверяем по ключевым словам
        level = check_keywords(text)
        
        if level == "strong":
            filtered_strong.append(e)
        elif level == "soft":
            filtered_soft.append(e)
        elif level is None:
            # Если вернуло None но НЕ из-за исключений (а просто нет ключей)
            # Дополнительно проверяем на исключения
            text_lower = text.lower()
            if not any(kw in text_lower for kw in EXCLUDE_KEYWORDS):
                all_fresh.append(e)
            else:
                excluded += 1

    print(f"Пропущено опубликованных: {skipped}")
    print(f"Исключено (спорт/блоги): {excluded}")
    print(f"По сильным ключам: {len(filtered_strong)}")
    print(f"По слабым ключам: {len(filtered_soft)}")
    print(f"Свежих технических: {len(all_fresh)}")

    # ПРИОРИТЕТ 1: Сильные ключевые слова
    if filtered_strong:
        filtered_strong.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("✅ Выбрана по СИЛЬНЫМ ключам")
        return filtered_strong[0]

    # ПРИОРИТЕТ 2: Слабые ключевые слова
    if filtered_soft:
        filtered_soft.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("✅ Выбрана по СЛАБЫМ ключам")
        return filtered_soft[0]

    # ПРИОРИТЕТ 3: Самая свежая техническая
    if all_fresh:
        all_fresh.sort(key=lambda x: x.get("published_parsed", datetime.now()), reverse=True)
        print("⚠️ Выбрана СВЕЖАЯ техническая (нет по фильтрам)")
        return all_fresh[0]

    print("❌ Подходящих статей не найдено")
    return None

# ---------------- OPENAI ----------------

def short_summary(title: str, summary: str) -> str:
    """Пост 197 символов с эмодзи внизу и хештегами"""
    news_text = f"{title}. {summary}" if summary else title
    prompt = (
        f"Создай пост для Telegram-канала:\n\n"
        f"НОВОСТЬ: {news_text}\n\n"
        f"ТРЕБОВАНИЯ:\n"
        f"1. Ровно 197 символов текста (считай внимательно!)\n"
        f"2. Добавь 2-3 эмодзи В КОНЦЕ текста\n"
        f"3. После текста на новой строке добавь 3-5 хештегов по теме\n"
        f"4. Пиши конкретно, без вводных фраз\n"
        f"5. Структура:\n"
        f"   [текст 197 символов] [эмодзи]\n\n"
        f"   #хештег1 #хештег2 #хештег3\n\n"
        f"6. ЗАПРЕЩЕНО: 'Что произошло', 'Какая проблема'\n"
        f"7. Пиши сразу по сути"
    )
    
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            frequency_penalty=0.7,
        )
        text = res.choices[0].message.content.strip()
        ps = "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        return text + ps
    except Exception as e:
        print(f"❌ OpenAI ошибка: {e}")
        short = (title[:180] + "...") if len(title) > 180 else title
        ps = "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        return f"{short} 🔐🌐\n\n#tech #новости{ps}"

def generate_image_prompt(title: str, summary: str) -> str:
    """Промпт для картинки 1:1"""
    base = f"Create short English prompt for 1:1 tech image about: {title}. Max 150 chars. Dark cyberpunk style, no text."
    
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": base}],
        )
        return res.choices[0].message.content.strip()[:150]
    except Exception as e:
        print(f"❌ Промпт ошибка: {e}")
        return f"Dark tech cyberpunk illustration, 1:1 square, no text"

def generate_image_pollinations(prompt: str) -> Optional[str]:
    """Генерация с увеличенным timeout и retry"""
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            print(f"Генерация картинки (попытка {attempt + 1}/{max_retries})...")
            url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
            params = {
                "width": "1024",
                "height": "1024",
                "nologo": "true",
                "model": "flux",
                "enhance": "false"  # отключаем улучшение для скорости
            }
            
            # Увеличенный timeout
            r = requests.get(url, params=params, timeout=90)
            if r.status_code != 200:
                print(f"HTTP {r.status_code}")
                continue
            
            filename = f"news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            with open(filename, "wb") as f:
                f.write(r.content)
            print(f"✅ Картинка сохранена: {filename}")
            return filename
            
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout при попытке {attempt + 1}")
            if attempt < max_retries - 1:
                await asyncio.sleep(3)
                continue
        except Exception as e:
            print(f"❌ Ошибка генерации: {e}")
            if attempt < max_retries - 1:
                continue
    
    print("❌ Не удалось сгенерировать картинку после всех попыток")
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
        print("Нет подходящих статей")
        return

    aid = art["id"]
    print(f"\n✅ Выбрана: {art['title']}")
    print(f"Источник: {art['source']}, Дата: {art['published_parsed']}\n")

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
            print("✅ Отправлено с картинкой")
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            print("✅ Отправлено БЕЗ картинки")

        save_posted(aid)
        
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

if __name__ == "__main__":
    asyncio.run(autopost())





















































