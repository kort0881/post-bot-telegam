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

# ============ ОБЯЗАТЕЛЬНЫЕ КЛЮЧЕВЫЕ СЛОВА ============

REQUIRE_KEYWORDS = [
    # VPN, Прокси, Туннелирование
    "vpn", "прокси", "туннель", "proxy", "tunnel",
    
    # Шифрование, Безопасность, Приватность
    "шифрование", "шифр", "encrypt", "приватность", "privacy",
    "безопасность", "security", "защита данных",
    
    # Интернет, Сеть
    "интернет", "интернета", "интернету", "internet", "сеть",
    "сети", "network", "протокол", "protocol",
    
    # Анонимность, Скрытие
    "анонимность", "анонимный", "anonymous", "скрытие", "скрывать",
    "incognito", "скрытый", "hidden", "маскировка",
    
    # Цензура, Блокировки
    "цензура", "блокировка", "блокиров", "блокир", "blocking",
    "censorship", "restrict", "ограничение", "запрет",
    
    # DNS, DPI, Фильтрация
    "dns", "dpi", "фильтр", "filter", "фильтрация",
    
    # Обход ограничений
    "обход", "bypass", "circumvent", "обходить", "обогнуть",
    
    # Российские органы (РКН, Минцифры, etc)
    "роскомнадзор", "ркн", "минцифры", "минцифр", "федсу",
    "заблокирова", "разблокир", "деблокир",
    
    # Технические термины
    "трафик", "traffic", "пакет", "packet", "соединение",
    "connection", "канал", "channel", "линия связи",
    "tor", "darknet", "darkweb", "луковая маршрутизация",
    "wireguard", "openvpn", "shadowsocks", "mtproto",
    "обфускация", "obfuscation", "маскировка трафика",
    
    # Нейросети (новое направление)
    "нейросеть", "нейросети", "ии", "ai", "искусственный интеллект",
    "llm", "gpt", "claude", "chatgpt",
]

# ============ ОБЯЗАТЕЛЬНО РОССИЯ ============
# НОВОСТЬ ДОЛЖНА СОДЕРЖАТЬ ХОТЯ БЫ ОДНО СЛОВО ОТСЮДА

RUSSIA_KEYWORDS = [
    "россия", "рф", "рф ", "российск", "россий",
    "москв", "питер", "санкт", "урал", "сибирь",
    "крым", "донецк", "луганск", "днр", "лнр",
]

# ============ ИСКЛЮЧИТЬ ============

EXCLUDE_KEYWORDS = [
    # Спорт
    "теннис", "футбол", "хоккей", "баскетбол", "волейбол",
    "спорт", "олимпиад", "чемпионат", "турнир", "матч",
    "игрок", "команда", "лига", "чемпион",
    
    # Развлечения / Игры
    "игра", "геймплей", "gameplay", "dungeon", "quest",
    "playstation", "xbox", "nintendo", "steam", "boss", "raid",
    "шутер", "mmorpg", "battle royale", "геймер", "gamer",
    "helldivers", "routine", "игровой", "игровых",
    
    # Личное / Блог
    "моя жизнь", "мой опыт", "как я", "моя история",
    "вернулся", "вернулась", "личный опыт", "я делаю",
    
    # Кино, ТВ, Музыка
    "кино", "фильм", "сериал", "музыка", "концерт",
    "актер", "режиссер", "песня", "клип", "видеоклип",
    "дайджест", "digest", "обзор игр", "новости игр",
    "премьера", "выпуск сезона",
    
    # Корпорации / Финансы
    "coca-cola", "pepsi", "nestle", "tesla",
    "samsung", "sony", "lg", "huawei",
    "компания сообщила", "компания объявила",
    "корпорация", "корпоративный",
    "акции", "биржа", "инвестор", "капитализация",
    "выручка", "прибыль", "убыток", "доход", "оборот",
    "финансовые результаты", "финансовый отчет",
    "отчетность", "квартальный отчет", "годовой отчет",
    "генеральный директор", "ceo", "cfo",
    "маркетинг", "бренд", "реклама", "кампания",
    "лонч продукта", "выход продукта", "новый продукт",
    
    # Политика (в общем)
    "выборы", "президент", "парламент", "закон",
    "политик", "политическ", "партия",
    
    # Медицина / Здоровье
    "болезнь", "заболева", "вирус", "covid", "коронавирус",
    "лекарство", "таблетка", "терапия", "лечение",
    
    # Криптовалюта
    "биткойн", "bitcoin", "эфириум", "ethereum",
    "крипто", "crypto", "блокчейн", "blockchain",
    
    # Автомобили
    "автомобиль", "машина", "авто", "car",
    "двигатель", "мотор", "бензин", "газ",
    
    # Судебные дела (если не про РФ и цензуру)
    "суд", "судебный", "судья", "апелляция", "иск",
    "австралия", "австралийский", "новая зеландия",
    "великобритания", "англия", "канада",
    
    # Социальные сети (если не про блокировку)
    "reddit", "twitter", "instagram", "tiktok",
    "facebook", "youtube ban",
    
    # Прочее
    "полнолуние", "астрономия", "космос",
    "погода", "климат", "температура",
    "животное", "животных", "питомец", "собака", "кошка",
    "еда", "рецепт", "кухня", "кулинар",
    "путешествие", "туризм", "отпуск",
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

# ============ ФИЛЬТРАЦИЯ ============

def check_require_keywords(text: str) -> bool:
    """Проверяет, есть ли хотя бы один REQUIRE ключ."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in REQUIRE_KEYWORDS)

def check_exclude_keywords(text: str) -> bool:
    """Проверяет, есть ли EXCLUDE ключи — если есть, отсеиваем."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in EXCLUDE_KEYWORDS)

def has_russia_mention(text: str) -> bool:
    """ОБЯЗАТЕЛЬНО: в новости должна быть РОССИЯ."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in RUSSIA_KEYWORDS)

# ============ ВЫБОР СТАТЬИ ============

def pick_article(articles: List[Dict]) -> Optional[Dict]:
    suitable_articles: List[Dict] = []
    skipped = 0
    excluded_require = 0
    excluded_blacklist = 0
    excluded_no_russia = 0

    for e in articles:
        aid = e.get("id")
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = f"{title} {summary}"

        # ШАГ 1: Проверка исключений
        if check_exclude_keywords(text):
            excluded_blacklist += 1
            continue

        # ШАГ 2: Проверка обязательных ключей
        if not check_require_keywords(text):
            excluded_require += 1
            continue

        # ШАГ 3: СТРОГО ОБЯЗАТЕЛЬНО - РОССИЯ В ТЕКСТЕ
        if not has_russia_mention(text):
            excluded_no_russia += 1
            continue

        # ✅ Все проверки пройдены
        suitable_articles.append(e)
        print(f"  ✅ Подходит: {title[:70]}")

    print(f"\n📊 Статистика фильтрации:")
    print(f"  Пропущено (уже были): {skipped}")
    print(f"  Исключено (чёрный список): {excluded_blacklist}")
    print(f"  Исключено (нет обязательных ключей): {excluded_require}")
    print(f"  Исключено (НЕТ РОССИИ в тексте): {excluded_no_russia}")
    print(f"  Подходят (ВСЕ условия): {len(suitable_articles)}")

    if not suitable_articles:
        print("❌ Нет статей про Россию!")
        return None

    suitable_articles.sort(
        key=lambda x: x.get("published_parsed", datetime.now()),
        reverse=True
    )
    chosen = suitable_articles[0]
    print(f"\n🎯 Выбрана: {chosen['title'][:80]}")
    return chosen

# ============ OPENAI TEXT ============

def short_summary(title: str, summary: str, link: str) -> str:
    news_text = f"{title}. {summary}" if summary else title
    prompt = (
        "Вот фрагмент новостной статьи. Сохрани факты максимально близко к тексту, "
        "перефразируй только чтобы читалось плавно.\n\n"
        f"{news_text}\n\n"
        "Сделай короткий новостной пост для Telegram:\n"
        "- Объём строго 450–550 символов.\n"
        "- Удали всё, что похоже на рекламу, маркетинговые формулировки, промо.\n"
        "- Никаких выдуманных деталей, только то, что есть в тексте.\n"
        "- В конце 2–3 релевантных хештега через пробел.\n"
        "- 1–2 эмодзи по смыслу внутри текста.\n"
        "- Не добавляй призыв на подписку или ссылку на канал."
    )

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=320,
        )
        text = res.choices[0].message.content.strip()

        if len(text) > 550:
            print(f"⚠️ Текст {len(text)} символов, режу до 550")
            text = text[:547] + "…"

        # Добавляем ссылку и PS
        ps = f"\n\n🔗 Оригинал: {link}\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        full_text = text + ps

        if len(full_text) > 1020:
            excess = len(full_text) - 1020
            text = text[:-(excess + 3)] + "…"
            full_text = text + ps

        print(f"📊 Итоговая длина: {len(full_text)} символов")
        return full_text

    except Exception as e:
        print(f"❌ OpenAI: {e}")
        fallback = f"{title}\n\n{(summary or '')[:400]}"
        return f"{fallback}\n\n🔗 {link}\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"

# ============ КАРТИНКИ (POLLINATIONS - БЕСПЛАТНО) ============

def generate_image(title: str) -> Optional[str]:
    """
    Картинка через Pollinations (бесплатно).
    Каждый раз новый seed, чтобы картинки были разные.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    seed = random.randint(0, 10_000_000)

    prompt_core = (
        f"realistic cinematic detailed illustration about {title[:100]}, "
        "modern cybersecurity and internet privacy, people using computers, "
        "daytime city or office, neutral natural colors, soft light, high detail, 4k, "
        "photo realistic, professional editorial photography, not cartoon, not anime. "
        "no cyberpunk, no neon lights, no sci-fi, no futuristic helmets, "
        "no glowing effects, no dystopia, no text, no logo, no watermark"
    )

    # Добавляем noise в промпт, чтобы ломать HTTP-кэш
    prompt = prompt_core + f" random detail id {seed}"

    print("🎨 Генерация через Pollinations")
    print(f"   Seed: {seed}")

    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}"

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

# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    articles = load_articles_from_sites()
    if not articles:
        print("Нет статей")
        return

    art = pick_article(articles)
    if not art:
        print("Нет статей про Россию с нужными ключами")
        return

    aid = art["id"]
    print(f"\n✅ Выбрана: {art['title']}")
    print(f"Источник: {art['source']}\n")

    try:
        text = short_summary(art["title"], art.get("summary", ""), art.get("link", ""))
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
            print("✅ Отправлено текстом (без картинки)")

        save_posted(aid)

    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")

async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
























































































