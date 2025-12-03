import os
import json
import asyncio
from datetime import datetime
from io import BytesIO

import requests
from openai import OpenAI
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from PIL import Image

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

# ===== Ключевые слова (жёстко по теме канала) =====
STRONG_KEYWORDS = [
    # VPN / обход / цензура
    "vpn", "впн", "прокси", "proxy", "tor", "shadowsocks",
    "wireguard", "openvpn", "ikev2",
    "обход блокировок", "обход цензуры", "анонимность",
    "роскомнадзор", "ркн",
    "интернет-цензура", "цензура в интернете",
    "блокировка сайтов", "блокировка ресурса",
    "реестр запрещенных сайтов", "ограничение доступа",
    "фильтрация трафика", "dpi", "deep packet inspection",

    # мессенджеры / соцсети
    "телеграм", "telegram",
    "whatsapp", "signal", "viber",
    "messenger", "мессенджер",

    # технологии и софт (без акцента на игры)
    "обновление безопасности", "патч безопасности",
    "антивирус", "firewall", "фаервол",
    "браузер", "браузер tor",
    "клиент vpn", "vpn-клиент",
]

SOFT_KEYWORDS = [
    # техно / ИИ / софт
    "приложение для пк", "desktop-приложение", "утилита для windows",
    "программа для macos", "open source",
    "искусственный интеллект", "нейросеть", "нейросети",
    "ai", "machine learning",
    "кибербезопасность", "информационная безопасность",
    "конфиденциальность в интернете", "privacy",
    "суверенный интернет", "ограничение интернета",

    # игры как часть техно-контекста (но не единственная тема)
    "онлайн-игра", "игровой сервис", "игровая платформа",
    "гейминг", "игровые сервера",
]

POSTED_FILE = "posted_articles.json"

# ===== Работа с уже опубликованными постами =====
if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        posted_articles = set(json.load(f))
else:
    posted_articles = set()


def save_posted(article_id: str) -> None:
    posted_articles.add(article_id)
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_articles), f, ensure_ascii=False, indent=2)


# ===== Утилиты парсинга =====
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


# ===== Парсинг 3DNews =====
def load_3dnews():
    url = "https://3dnews.ru/"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    parts = html.split('<a href="/')
    for part in parts[1:10]:
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

        articles.append(
            {
                "id": link,
                "title": title,
                "summary": summary,
                "link": link,
                "published_parsed": datetime.now(),
            }
        )

    print("DEBUG: статей из 3DNews:", len(articles))
    return articles


# ===== Парсинг Хабра =====
def load_habr():
    url = "https://habr.com/ru/feed/"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    chunks = html.split("<article")
    for chunk in chunks[1:8]:
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
            summary = clean_text(chunk[p_start:p_end])
        else:
            summary = ""

        articles.append(
            {
                "id": link,
                "title": title,
                "summary": summary,
                "link": link,
                "published_parsed": datetime.now(),
            }
        )

    print("DEBUG: статей из Хабра:", len(articles))
    return articles


# ===== Парсинг Tproger =====
def load_tproger():
    url = "https://tproger.ru/news"
    html = safe_get(url)
    if not html:
        return []

    articles = []
    parts = html.split('<a ')
    for part in parts[1:12]:
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

        articles.append(
            {
                "id": link,
                "title": title,
                "summary": summary,
                "link": link,
                "published_parsed": datetime.now(),
            }
        )

    print("DEBUG: статей из Tproger:", len(articles))
    return articles


# ===== Сбор всех статей =====
def load_articles_from_sites():
    articles = []
    articles.extend(load_3dnews())
    articles.extend(load_habr())
    articles.extend(load_tproger())
    print("DEBUG: всего статей из сайтов:", len(articles))
    return articles


# ===== Фильтрация и выбор статьи =====
def filter_article(entry):
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    text = (title + " " + summary).lower()

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
            key=lambda x: (
                x[0],
                x[1].get("published_parsed", datetime.now()),
            ),
            reverse=True,
        )
        return scored[0][1]

    return None


# ===== Генерация текста =====
def short_summary(title: str, summary: str) -> str:
    prompt = (
        "Сделай новостной пост для Telegram‑канала про VPN, анонимность, "
        "интернет‑цензуру, мессенджеры и технологии.\n"
        "Требования к тексту:\n"
        "1) 4–6 предложений, 500–650 символов.\n"
        "2) Стиль техно‑новостей: факты, минимум воды, понятный язык.\n"
        "3) Структура:\n"
        "   - 1 предложение: что произошло и почему это важно.\n"
        "   - 2–3 предложения: суть изменения/события, чем отличается от старого.\n"
        "   - 1–2 предложения: вывод для обычного пользователя "
        "(конфиденциальность, блокировки, безопасность, удобство).\n"
        "4) Не используй ссылки и эмодзи. Не упоминай источники.\n"
        "5) В конце добавь отдельной строкой: "
        "PS: \"Кто за ключами — https://t.me/+EdEfIkn83Wg3ZTE6\".\n"
        "6) Хэштеги не пиши — они будут добавлены отдельно.\n\n"
        f"Заголовок новости: {title}\n"
        f"Краткое описание/лид: {summary or 'краткое описание отсутствует, сгенерируй сам по заголовку'}\n"
        "Сгенерируй только текст поста без лишних пояснений."
    )
    result = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    content = result.choices[0].message.content.strip()
    return content[:650].rstrip()


# ===== Генерация картинки (обязательна) =====
def get_img_from_dalle(title: str):
    prompt = (
        f"Реалистичное, детализированное изображение к новости: '{title}', "
        "в стиле технологичных иллюстраций. Акцент на интернет‑технологиях, "
        "цифровой безопасности, VPN, мессенджерах или сетевой инфраструктуре."
    )
    try:
        print("DEBUG DALL-E PROMPT:", prompt[:200])
        resp = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
        img_url = resp.data[0].url
        img = Image.open(BytesIO(requests.get(img_url).content))
        tmp = f"temp_{int(datetime.now().timestamp())}.png"
        img.save(tmp)
        return tmp
    except Exception as e:
        print("Ошибка генерации картинки DALL-E:", repr(e))
        return None


# ===== Автопостинг =====
async def autopost():
    articles = load_articles_from_sites()

    if not articles:
        print("Вообще нет статей, пост пропущен")
        return

    art = pick_article(articles)

    # Жёстко: если нет статьи по тематике — не постим
    if not art:
        print("Нет подходящих статей по тематике (VPN/интернет/софт), пост пропущен")
        return

    article_id = art.get("id", art.get("link", art.get("title")))
    if article_id in posted_articles:
        print("Эта статья уже была опубликована")
        return

    title = art.get("title", "")
    summary = art.get("summary", "")[:400]
    news = short_summary(title, summary)

    all_keywords = STRONG_KEYWORDS + SOFT_KEYWORDS
    text_for_tags = (title + " " + summary).lower()
    hashtags = [
        f"#{kw.replace(' ', '')}"
        for kw in all_keywords
        if kw.lower() in text_for_tags
    ]
    hashtags += ["#Новости", "#Telegram", "#Канал"]

    caption = f"{news}\n\n{' '.join(hashtags)}"

    img_path = get_img_from_dalle(title)

    # Картинка обязательна
    if not img_path:
        print("Картинку сгенерировать не удалось, пост не отправлён")
        return

    with open(img_path, "rb") as ph:
        await bot.send_photo(CHANNEL_ID, ph, caption=caption)
    os.remove(img_path)

    save_posted(article_id)
    print("[OK]", datetime.now(), "-", title)


# ===== Основная функция =====
async def main():
    await autopost()


if __name__ == "__main__":
    asyncio.run(main())







