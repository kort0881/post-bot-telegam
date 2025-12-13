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
    # Нейросети
    "нейросеть", "нейросети", "ии", "ai", "искусственный интеллект",
    "llm", "gpt", "claude", "chatgpt",
]

# ============ РОССИЯ (для приоритета, не обязательно) ============

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
    # Политика
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
    # Судебные дела (в основном нерф)
    "суд", "судебный", "судья", "апелляция", "иск",
    "австралия", "австралийский", "новая зеландия",
    "великобритания", "англия", "канада",
    # Соцсети (если не про блокировку)
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
    text_lower = text.lower()
    return any(kw in text_lower for kw in REQUIRE_KEYWORDS)

def check_exclude_keywords(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in EXCLUDE_KEYWORDS)

def has_russia_mention(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in RUSSIA_KEYWORDS)

def filter_articles(articles: List[Dict]) -> List[Dict]:
    suitable_ru: List[Dict] = []
    suitable_world: List[Dict] = []

    skipped = 0
    excluded_require = 0
    excluded_blacklist = 0

    for e in articles:
        aid = e.get("id")
        if aid in posted_articles:
            skipped += 1
            continue

        title = e.get("title", "")
        summary = e.get("summary", "")
        text = f"{title} {summary}"

        if check_exclude_keywords(text):
            excluded_blacklist += 1
            continue

        if not check_require_keywords(text):
            excluded_require += 1
            continue

        if has_russia_mention(text):
            suitable_ru.append(e)
        else:
            suitable_world.append(e)

    print(f"\n📊 Фильтрация:")
    print(f"  Пропущено (уже были): {skipped}")
    print(f"  Исключено (чёрный список): {excluded_blacklist}")
    print(f"  Исключено (нет обязательных ключей): {excluded_require}")
    print(f"  РФ-новости: {len(suitable_ru)}")
    print(f"  World-новости: {len(suitable_world)}")

    target = suitable_ru if suitable_ru else suitable_world
    target.sort(
        key=lambda x: x.get("published_parsed", datetime.now()),
        reverse=True
    )
    return target

# ============ ФИЛЬТР «НИ О ЧЁМ» ============

BAD_PHRASES = [
    "в мире программного обеспечения продолжаются",
    "компании обновляют свои платформы и инструменты",
    "оставайтесь в курсе актуальных трендов и технологий",
    "важно следить за последними обновлениями",
]

def is_too_generic(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in BAD_PHRASES):
        return True
    return False

# ============ OPENAI TEXT ============

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
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
        "- Не добавляй призыв на подписку или ссылку на канал.\n"
        "- Не используй общие фразы типа 'в мире программного обеспечения продолжаются изменения'."
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

        if is_too_generic(text):
            print("⚠️ Текст слишком общий, пропускаем эту статью")
            return None

        # Добавляем ТОЛЬКО PS про ключи, без ссылки на оригинал
        ps = "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"
        full_text = text + ps

        # ограничение телеги 1024 символа на caption
        if len(full_text) > 1020:
            excess = len(full_text) - 1020
            text = text[:-(excess + 3)] + "…"
            full_text = text + ps

        return full_text

    except Exception as e:
        print(f"❌ OpenAI: {e}")
        fallback_core = f"{title}\n\n{(summary or '')[:400]}"
        if is_too_generic(fallback_core):
            return None
        return fallback_core + "\n\nPS💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ZTE6"

# ============ КАРТИНКИ (POLLINATIONS – РАЗНЫЕ СТИЛИ) ============

def generate_image(title: str) -> Optional[str]:
    """
    Картинка через Pollinations с разными сценами и стилями.
    Без киберпанка и неоновых огней.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    seed = random.randint(0, 10_000_000)
    noise = random.randint(0, 10_000_000)

    scene_options = [
        "people connecting through secure lines over a world map",
        "abstract data streams flowing between servers",
        "shield protecting data cubes in cyberspace",
        "person using a laptop at home in a calm atmosphere",
        "minimalistic shapes symbolizing secure internet connection",
        "router and laptop on a desk with glowing network cables",
        "group of people using smartphones with shield icons around them",
    ]

    style_options = [
        "flat vector illustration, clean minimal style",
        "isometric illustration, detailed but simple",
        "semi-realistic digital painting with soft shading",
        "3d render with soft lighting, realistic materials",
        "editorial illustration for a technology magazine",
    ]

    color_options = [
        "warm pastel colors",
        "cool blue and teal palette",
        "black and white with one vivid accent color",
        "soft muted colors, low contrast",
        "light neutral colors with subtle gradients",
    ]

    scene = random.choice(scene_options)
    style = random.choice(style_options)
    colors = random.choice(color_options)

    prompt = (
        f"unique id {timestamp}_{noise}, "
        f"{scene}, "
        f"about: {title[:120]}, "
        f"{style}, {colors}, "
        "related to cybersecurity, internet privacy and vpn usage, "
        "no cyberpunk, no neon lights, no sci-fi, "
        "no futuristic helmets, no dystopia, "
        "no text, no logo, no watermark"
    )

    print("🎨 Генерация через Pollinations")
    print(f"   Seed: {seed}")
    print(f"   Noise: {noise}")
    print(f"   Scene: {scene}")
    print(f"   Style: {style}")
    print(f"   Colors: {colors}")

    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}"  # [web:22]

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

    candidates = filter_articles(articles)
    if not candidates:
        print("Нет статей по нужным ключам")
        return

    text_to_post = None
    chosen_article = None

    # пробуем несколько статей подряд, пока не получим нормальный текст
    for art in candidates[:10]:
        print(f"\n🔍 Пробуем статью: {art['title']}")
        txt = short_summary(art["title"], art.get("summary", ""), art.get("link", ""))
        if txt:
            text_to_post = txt
            chosen_article = art
            break
        else:
            print("⏭️ Статья отброшена (общий текст)")

    if not text_to_post or not chosen_article:
        print("❌ Не удалось получить нормальный текст для поста")
        return

    aid = chosen_article["id"]
    print(f"\n✅ Выбрана: {chosen_article['title']}")
    print(f"Источник (в пост не идёт): {chosen_article['source']}\n")

    try:
        img_file = generate_image(chosen_article["title"])

        if img_file and os.path.exists(img_file):
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=FSInputFile(img_file),
                caption=text_to_post,
            )
            os.remove(img_file)
            print("✅ Отправлено с картинкой")
        else:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text_to_post,
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



























































































