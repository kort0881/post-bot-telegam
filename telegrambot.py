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

# ---------------- KEYWORDS (STRONG) ----------------

STRONG_KEYWORDS = [
    "интернет", "vpn", "прокси", "шифрование", "анонимность", "приватность",
    "трафик", "обход блокировок", "обход цензуры", "цензура", "блокировка сайтов",
    "роскомнадзор", "ркн", "минцифры", "суверенный интернет", "белые списки",
    "черные списки", "тспу", "dpi", "глубокая инспекция трафика", "обфускация",
    "туннелирование", "маскировка трафика", "маскировка ip", "скрытие адреса",
    "приватный доступ", "безопасный доступ", "теневой трафик", "скрытый трафик",
    "резолвер", "альтернативный dns", "защищенный dns", "l2tp", "ipsec",
    "openvpn", "wireguard", "shadowsocks", "mtproto", "tor", "darknet",
    "мосты tor", "узлы tor", "прокси сервер", "прокси цепочка", "ротация прокси",
    "фильтрация трафика", "антиблокировка", "антидпи", "обход фаервола", "фаервол",
    "сетевые ограничения", "обход ограничений", "приватный канал", "шифрованный канал",
    "защищенный канал", "интернет свобода", "цифровая свобода", "сетевой контроль",
    "интернет контроль", "анализ трафика", "скрытие трафика", "защищенная связь",
    "приватная связь", "безопасная связь", "разрешенный трафик", "запрещенный трафик",
    "обход запретов", "анти мониторинг", "анти слежка", "цифровая защита",
    "сетевые атаки", "сетевые фильтры", "интернет фильтры", "стелс режим",
    "скрытый режим", "безопасный протокол", "альтернативный протокол",
    "туннельный протокол", "сетевой туннель", "зашифрованный туннель",
    "приватный туннель", "скрытый туннель", "защищённый сервер", "анонимный сервер",
    "приватный сервер", "обход трекинга", "защита данных", "конфиденциальность",
    "доступ без ограничений", "доступ к сети", "заблокированные сайты",
    "доступ к сервисам", "нейросети", "ии", "искусственный интеллект",
    "ai-анализ", "ai-безопасность", "нейросетевой контроль", "ai-фильтрация",
    "ai-обход", "нейросетевые алгоритмы",
]

# ---------------- EXCLUDE (спорт, игры, бизнес) ----------------

EXCLUDE_KEYWORDS = [
    # спорт / развлечения / личное
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

    # бизнес / корпорации
    "coca-cola", "coca cola", "pepsi", "nestle", "tesla", "apple",
    "meta", "google", "microsoft", "amazon", "samsung", "sony",
    "компания сообщила", "компания объявила",
    "корпорация", "корпоративный", "корпоративная",
    "акции", "биржа", "инвестор", "инвестиции", "капитализация",
    "выручка", "прибыль", "убыток", "доход", "оборот",
    "финансовые результаты", "финансовый отчет",
    "отчетность", "квартальный отчет", "годовой отчет",
    "генеральный директор", "ceo", "cfo", "совет директоров",
    "топ-менеджмент", "менеджмент компании",
    "маркетинг", "бренд", "брендовый", "реклама",
    "рекламная кампания", "кампания бренда",
    "лонч продукта", "выход продукта",
    "новый продукт", "новая линейка",
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

# ---------------- PARSERS (3 САЙТА) ----------------

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
                    desc_chunk = part[desc_start:desc_start + 700]
                    p_start = desc_chunk.find(">")
                    if p_start != -1:
                        p_end = desc_chunk.find("</", p_start)
                        if p_end != -1:
                            summary = clean_text(desc_chunk[p_start + 1:p_end])[:700]

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
                summary = clean_text(
                    entry.get("summary") or entry.get("description") or ""
                )[:700]
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
    print(f"ВСЕГО: {len(articles)} статей до фильтрации")
    return articles

# ---------------- FILTER (ТОЛЬКО STRONG) ----------------

def check_keywords_strong(text: str) -> bool:
    text_lower = text.lower()

    if any(kw in text_lower for kw in EXCLUDE_KEYWORDS):
        return False

    return any(kw in text_lower for kw in STRONG_KEYWORDS)

# ---------------- PICK ARTICLE ----------------

def pick_article(articles: List[Dict]) -> Optional[Dict]:
    strong_articles: List[Dict] = []
    skipped = 0
    excluded = 0

    for e in articles:
        aid = e.get("id")
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = f"{title} {summary}"

        if not check_keywords_strong(text):
            excluded += 1
            continue

        strong_articles.append(e)

    print(f"Пропущено (уже были): {skipped}")
    print(f"Отсеяно по ключам/исключениям: {excluded}")
    print(f"Сильных по ключам: {len(strong_articles)}")

    if not strong_articles:
        return None

    strong_articles.sort(
        key=lambda x: x.get("published_parsed", datetime.now()),
        reverse=True
    )
    print("✅ Выбор только из СИЛЬНЫХ по ключам (только STRONG)")
    return strong_articles[0]

# ---------------- OPENAI TEXT (500–600) ----------------

def short_summary(title: str, summary: str) -> str:
    news_text = f"{title}. {summary}" if summary else title
    prompt = (
        "Вот фрагмент новостной статьи. Сохрани факты максимально близко к тексту, "
        "перефразируй только чтобы читалось плавно.\n\n"
        f"{news_text}\n\n"
        "Сделай короткий новостной пост для Telegram:\n"
        "- Объём строго 500–600 символов.\n"
        "- Удали всё, что похоже на рекламу, маркетинговые формулировки, промо, призывы купить/подписаться.\n"
        "- Никаких выдуманных деталей, только то, что есть в тексте.\n"
        "- В начале одно короткое предложение контекста, дальше сухие факты из статьи.\n"
        "- В конце 2–3 релевантных хештега через пробел.\n"
        "- 1–2 эмодзи по смыслу внутри текста."
    )

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=350,
        )
        text = res.choices[0].message.content.strip()

        if len(text) > 600:
            print(f"⚠️ Текст {len(text)} символов, режу до 600")
            text = text[:597] + "…"
        elif len(text) < 500:
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
        fallback = f"{title}\n\n{(summary or '')[:520]}"
        return f"{fallback} 🔐🌐\n\n#tech #новости\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"

# ---------------- IMAGE GENERATION (Pollinations) ----------------

def generate_image(title: str) -> Optional[str]:
    """
    Картинка через Pollinations, максимально реалистичная,
    без киберпанка и неона.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    prompt = (
        f"realistic cinematic illustration about {title[:80]}, "
        "modern cybersecurity, internet privacy and censorship bypass, "
        "professional corporate style, clean composition, neutral colors, "
        "sharp focus, high detail, 4k, photography style. "
        "no cyberpunk, no neon, no sci-fi, no futuristic city, "
        "no glowing effects, no dystopia, no text on image"
    )

    print("🎨 Генерация через Pollinations")
    print(f"   Промпт: {prompt[:140]}...")

    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}"

        resp = requests.get(url, timeout=120)
        if resp.status_code != 200:
            print(f"❌ Pollinations HTTP {resp.status_code}")
            return None

        filename = f"news_{timestamp}_{random.randint(1000,9999)}.jpg"
        with open(filename, "wb") as f:
            f.write(resp.content)

        print(f"✅ Картинка сохранена: {filename}")
        return filename

    except Exception as e:
        print(f"❌ Ошибка Pollinations: {e}")
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
        print("Нет подходящих по СИЛЬНЫМ ключам")
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

async def main():
    try:
        await autopost()
    finally:
        session = await bot.get_session()
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())

















































































