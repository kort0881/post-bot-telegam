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
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from openai import OpenAI

# ============ CONFIG ============

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

# Безопасный лимит подписи к медиа в Telegram (реальный лимит ~1024 символа)[web:54][web:48]
TELEGRAM_CAPTION_LIMIT = 1000

# ============ СТИЛИ ПОСТОВ (СТРОГО НОВОСТНЫЕ) ============

POST_STYLES = [
    {
        "name": "news_report",
        "intro": "Ты — техно-журналист. Излагаешь факты сухо и нейтрально.",
        "tone": "Сдержанный, информативный, безоценочный.",
        "emojis": "🤖📊"
    },
    {
        "name": "explainer",
        "intro": "Ты — научный редактор. Объясняешь суть без эмоций.",
        "tone": "Нейтральный, пояснительный, сухой.",
        "emojis": "🧠🔍"
    }
]

POST_STRUCTURES = [
    "inverted_pyramid",
    "straight_news"
]

# ============ МЯГКИЙ АНТИРЕКЛАМНЫЙ ФИЛЬТР ============

HARD_BAD_PHRASES = [
    "must-have", "must have", "обязательно оцените",
    "не упустите", "успейте", "только сейчас", "прямо сейчас",
    "поспешите", "убийца всего", "killer фича",
    "лучшее решение", "идеальный инструмент",
]

def is_too_promotional(text: str) -> bool:
    """Проверяет текст только на откровенно продающие формулировки."""
    low = text.lower()
    return any(phrase in low for phrase in HARD_BAD_PHRASES)

# ============ ПРИОРИТЕТ: ИИ И НЕЙРОСЕТИ ============

AI_KEYWORDS = [
    "нейросеть", "нейросети", "нейронная сеть", "ии", "искусственный интеллект",
    "neural network", "artificial intelligence",
    "llm", "gpt", "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум", "deepseek", "grok",
    "openai", "anthropic", "deepmind", "сбер ai", "яндекс ai",
    "hugging face", "stability ai", "meta ai", "google ai",
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    "генерация видео", "text-to-image", "text-to-video",
    "машинное обучение", "глубокое обучение", "transformer",
    "трансформер", "языковая модель", "мультимодальный",
    "дообучение", "обучение модели", "датасет", "fine-tuning",
    "чат-бот", "голосовой помощник", "автопилот", "распознавание",
    "нейросетевой", "ai-ассистент", "умный помощник",
    "компьютерное зрение", "обработка языка", "nlp",
    "agi", "рассуждение", "агент", "ai-агент", "контекстное окно",
    "токен", "большая языковая модель", "reasoning",
    "обучение с подкреплением", "rlhf", "промпт", "prompt"
]

TECH_KEYWORDS = [
    "представил", "анонсировал", "выпустил", "релиз", "запустил",
    "новинка", "дебют", "презентация", "показал", "unveiled",
    "смартфон", "ноутбук", "гаджет", "девайс", "устройство",
    "носимая электроника", "умные часы", "наушники",
    "робот", "робототехника", "дрон", "беспилотник", "автопилот",
    "автономный", "boston dynamics", "tesla bot",
    "квантовый", "квантовый компьютер", "процессор", "чип",
    "gpu", "видеокарта", "nvidia", "amd", "intel", "apple m",
    "spacex", "starship", "космос", "ракета", "спутник",
    "starlink", "nasa", "роскосмос",
    "виртуальная реальность", "дополненная реальность",
    "vr", "ar", "meta quest", "apple vision",
    "электромобиль", "tesla", "электрокар", "батарея", "аккумулятор",
    "прорыв", "инновация", "технология"
]

DISCOVERY_KEYWORDS = [
    "исследователи", "учёные", "ученые", "исследование", "исследования",
    "лаборатория", "университет", "институт",
    "mit", "stanford", "oxford", "berkeley", "cambridge",
    "arxiv", "preprint", "научная работа", "научная статья",
    "обнаружили", "обнаружен", "нашли", "выяснили", "доказали",
    "разработали новый метод", "новый алгоритм", "новый подход",
    "state-of-the-art", "sota", "benchmark", "dataset"
]

EXCLUDE_KEYWORDS = [
    "акции", "акция", "биржа", "котировки", "индекс",
    "инвестиции", "инвестор", "инвесторы", "дивиденды",
    "ipo", "капитализация", "рыночная стоимость",
    "выручка", "прибыль", "убыток", "доход", "оборот",
    "финансовый отчёт", "финансовый отчет", "квартальный отчёт",
    "миллиард долларов", "миллион долларов", "млрд", "млн рублей",
    "курс доллара", "курс евро", "курс рубля", "валюта",
    "цб", "центробанк", "ставка", "ключевая ставка", "инфляция",
    "экономика", "экономический", "ввп", "рецессия",
    "банк", "кредит", "ипотека", "вклад", "депозит",
    "фонд", "венчурный", "раунд финансирования",
    "сделка", "слияние", "поглощение", "m&a",
    "рынок", "доля рынка", "конкуренты",
    "цена акций", "стоимость компании", "оценка компании",
    "выход на биржу", "размещение", "листинг",
    "назначен", "назначение", "отставка", "уволен",
    "генеральный директор", "ceo", "основатель ушёл",
    "сокращение штата", "увольнения", "сокращения",
    "офис", "штаб-квартира", "переезд компании",
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч",
    "олимпиада", "чемпионат", "турнир", "сборная",
    "игра", "геймплей", "playstation", "xbox", "steam", "nintendo",
    "видеоигра", "консоль", "gaming",
    "кино", "фильм", "сериал", "музыка", "концерт", "актёр", "актер",
    "премьера", "трейлер", "netflix", "кинотеатр",
    "выборы", "президент", "парламент", "политик", "депутат",
    "санкции", "правительство", "министр", "закон", "законопроект",
    "болезнь", "covid", "пандемия", "грипп", "вакцина",
    "крипто", "bitcoin", "биткойн", "биткоин", "ethereum",
    "nft", "блокчейн", "криптовалюта", "майнинг",
    "суд", "судебный", "арест", "приговор", "тюрьма", "штраф",
    "иск", "антимонопольный"
]

# ============ STATE ============

posted_articles: Dict[str, Optional[float]] = {}

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            posted_articles = {item["id"]: item.get("timestamp") for item in posted_data}
        except Exception:
            posted_articles = {}


def save_posted_articles() -> None:
    data = [{"id": id_str, "timestamp": ts} for id_str, ts in posted_articles.items()]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_old_posts() -> None:
    global posted_articles
    now = datetime.now().timestamp()
    cutoff = now - (RETENTION_DAYS * 86400)
    posted_articles = {
        id_str: ts for id_str, ts in posted_articles.items()
        if ts is None or ts > cutoff
    }
    save_posted_articles()


def save_posted(article_id: str) -> None:
    posted_articles[article_id] = datetime.now().timestamp()
    save_posted_articles()


# ============ HELPERS ============

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


def detect_topic(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()

    if any(kw in text for kw in ["gpt", "chatgpt", "claude", "llm", "языковая модель"]):
        return "llm"
    elif any(kw in text for kw in ["midjourney", "dall-e", "stable diffusion", "генерация изображ"]):
        return "image_gen"
    elif any(kw in text for kw in ["робот", "robot", "автономн"]):
        return "robotics"
    elif any(kw in text for kw in ["spacex", "космос", "ракета", "спутник"]):
        return "space"
    elif any(kw in text for kw in ["nvidia", "gpu", "процессор", "чип"]):
        return "hardware"
    elif any(kw in text for kw in ["нейросет", "neural", "ии", "ai", "искусственный интеллект"]):
        return "ai"
    else:
        return "tech"


def get_hashtags(topic: str) -> str:
    hashtag_map = {
        "llm": "#ИИ #LLM #нейросети",
        "image_gen": "#ИИ #генерация #нейросети",
        "robotics": "#роботы #технологии",
        "space": "#космос #технологии",
        "hardware": "#железо #технологии",
        "ai": "#ИИ #нейросети",
        "tech": "#технологии #новости"
    }
    return hashtag_map.get(topic, "#технологии")


# ============ PARSERS ============

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"⚠️ RSS недоступен: {source}")
            return articles
    except Exception as e:
        print(f"❌ Ошибка загрузки RSS {source}: {e}")
        return articles

    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        if not link or link in posted_articles:
            continue
        articles.append({
            "id": link,
            "title": clean_text(entry.get("title") or ""),
            "summary": clean_text(
                entry.get("summary") or entry.get("description") or ""
            )[:700],
            "link": link,
            "source": source,
            "published_parsed": datetime.now()
        })

    if articles:
        print(f"✅ {source}: {len(articles)} статей")

    return articles


def load_articles_from_sites() -> List[Dict]:
    articles: List[Dict] = []

    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru",
        "Habr AI"
    ))
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru",
        "Habr ML"
    ))
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru",
        "Habr Neural"
    ))
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/data_science/all/?fl=ru",
        "Habr DS"
    ))
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/natural_language_processing/all/?fl=ru",
        "Habr NLP"
    ))
    articles.extend(load_rss(
        "https://habr.com/ru/rss/hub/robotics/all/?fl=ru",
        "Habr Robotics"
    ))
    articles.extend(load_rss("https://tproger.ru/feed/", "Tproger"))
    articles.extend(load_rss("https://hightech.fm/feed", "Хайтек"))
    articles.extend(load_rss("https://nplus1.ru/rss", "N+1"))
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://servernews.ru/rss", "ServerNews"))

    return articles


def filter_articles(articles: List[Dict]) -> List[Dict]:
    ai_discovery = []
    ai_other = []
    tech_discovery = []

    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()

        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

        is_ai = any(kw in text for kw in AI_KEYWORDS)
        is_tech = any(kw in text for kw in TECH_KEYWORDS)
        is_discovery = any(kw in text for kw in DISCOVERY_KEYWORDS)

        if is_ai and is_discovery:
            ai_discovery.append(e)
        elif is_ai:
            ai_other.append(e)
        elif is_tech and is_discovery:
            tech_discovery.append(e)

    for lst in (ai_discovery, ai_other, tech_discovery):
        lst.sort(key=lambda x: x["published_parsed"], reverse=True)

    return ai_discovery + ai_other + tech_discovery


# ============ ГЕНЕРАЦИЯ ТЕКСТА (МЯГКИЕ ОГРАНИЧЕНИЯ) ============

def build_dynamic_prompt(title: str, summary: str, style: dict, structure: str) -> str:
    news_text = f"Заголовок: {title}\n\nТекст: {summary}"

    structure_instructions = {
        "inverted_pyramid": """
Структура:
1. ЛИД — что произошло и главное новое (2–3 предложения).
2. ДЕТАЛИ — как это работает, какие результаты, примеры применения (3–4 предложения).
3. КОНТЕКСТ — к какому направлению ИИ/ML это относится и чем может быть полезно (1–2 предложения).
Заверши мысль так, чтобы текст выглядел законченно, без обрыва.
""",
        "straight_news": """
Структура:
1. ГЛАВНОЕ — что произошло и зачем это делали (2 предложения).
2. ПОДРОБНОСТИ — ключевые факты, метод, результаты, ограничения (3–4 предложения).
3. ИТОГ — какое это даёт направление для дальнейшего развития или применения (1–2 предложения).
Заверши абзацем с чётким выводом, а не обрывом.
"""
    }

    prompt = f"""
Ты — редактор новостной ленты об ИИ и исследованиях. Перепиши новость в формате расширенной заметки.

Стиль: {style['tone']}

НОВОСТЬ:
{news_text}

{structure_instructions.get(structure, structure_instructions['straight_news'])}

ТРЕБОВАНИЯ:
• Цель: около 700 символов. Допустимый диапазон: от 400 до 1000 символов.
• Только русский язык.
• Можно использовать 1–3 нейтральных эмодзи из набора {style['emojis']} и общих тех-эмодзи (⚙️, 💻, 📡, 📈, 🛰️), если они помогают визуально структурировать текст.
• Текст должен выглядеть как законченный абзац: с вводом, деталями и чётким финальным выводом.
• Пиши в третьем лице, без обращения к читателю.

ЗАПРЕЩЕНО:
• Любые прямые призывы: «попробуйте», «оцените», «не пропустите» и т.п.
• Явно рекламный и продающий тон.
• Обращения к читателю: «вы», «мы», «друзья».
• Клише: «будущее наступило», «мир изменился», «новая эра» и т.п.
• Придумывать факты, которых нет в исходной новости.

Выдай только основной текст поста, без хештегов и ссылок.
"""
    return prompt


def decorate_post(text: str, topic: str) -> str:
    """Аккуратно украшает пост: эмодзи-линия вверху + разделитель."""
    topic_icon_map = {
        "llm": "🤖",
        "ai": "🧠",
        "image_gen": "🎨",
        "robotics": "🦾",
        "hardware": "💻",
        "space": "🛰️",
        "tech": "⚙️"
    }
    icon = topic_icon_map.get(topic, "⚙️")
    top_line = f"{icon} {icon} {icon}"
    separator = "\n\n— — —\n\n"
    return f"{top_line}\n\n{text}{separator}"


def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    """Генерирует новостной пост, проверяет длину и мягко фильтрует рекламу."""
    style = random.choice(POST_STYLES)
    structure = random.choice(POST_STRUCTURES)

    print(f"   📝 Стиль: {style['name']}, структура: {structure}")

    prompt = build_dynamic_prompt(title, summary, style, structure)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — новостной редактор. Пишешь по фактам, без явной рекламы, "
                        "но допускается нейтральная оценочная лексика без продажного тона."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
            max_tokens=900,
        )
        core = res.choices[0].message.content.strip()

        if core.startswith('"') and core.endswith('"'):
            core = core[1:-1]
        if core.startswith('«') and core.endswith('»'):
            core = core[1:-1]

        length = len(core)
        if length < 250:
            print(f"   ⚠️ Текст слишком короткий (len={length}), пропускаем")
            return None

        if is_too_promotional(core):
            print("   ⚠️ Текст содержит жёстко рекламные формулировки, пропускаем")
            return None

        topic = detect_topic(title, summary)
        decorated = decorate_post(core, topic)

        hashtags = get_hashtags(topic)
        hashtag_line = f"\n{hashtags}"
        source_line = f"\n\n🔗 <a href=\"{link}\">Источник</a>"

        final_text = decorated + hashtag_line + source_line

        # Жёстко ограничиваем длину подписи, чтобы не ловить Bad Request от Telegram[web:54][web:48]
        if len(final_text) > TELEGRAM_CAPTION_LIMIT:
            print(
                f"   ⚠️ Подпись длиннее лимита (len={len(final_text)}), "
                f"обрезаем до {TELEGRAM_CAPTION_LIMIT}"
            )
            final_text = final_text[:TELEGRAM_CAPTION_LIMIT]

        return final_text

    except Exception as e:
        print(f"❌ OpenAI ошибка: {e}")
        return None


# ============ ГЕНЕРАЦИЯ КАРТИНОК ============

def generate_image(title: str, max_retries: int = 3) -> Optional[str]:
    image_styles = [
        "minimalist tech illustration, soft gradients, ",
        "abstract geometric visualization, ",
        "clean digital art, modern, ",
        "professional tech render, "
    ]

    style = random.choice(image_styles)

    for attempt in range(max_retries):
        seed = random.randint(0, 10**7)
        clean_title = title[:60].replace('"', '').replace("'", "").replace('\n', ' ')

        prompt = (
            f"{style}{clean_title}, "
            "technology, innovation, "
            "4k, no text, no letters, clean composition"
        )

        try:
            encoded = urllib.parse.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024&nologo=true"

            print(f"   🎨 Генерация изображения (попытка {attempt + 1}/{max_retries})...")

            resp = requests.get(url, timeout=90, headers=HEADERS)

            if resp.status_code == 200:
                content_type = resp.headers.get('content-type', '')
                if 'image' in content_type and len(resp.content) > 10000:
                    fname = f"img_{seed}.jpg"
                    with open(fname, "wb") as f:
                        f.write(resp.content)
                    print(f"   ✅ Изображение сохранено: {fname}")
                    return fname
                else:
                    print(f"   ⚠️ Получен неверный контент (size: {len(resp.content)})")
            else:
                print(f"   ⚠️ HTTP {resp.status_code}")

        except requests.Timeout:
            print(f"   ⚠️ Таймаут при генерации изображения")
        except requests.RequestException as e:
            print(f"   ⚠️ Ошибка сети: {e}")
        except Exception as e:
            print(f"   ❌ Неожиданная ошибка: {e}")

        if attempt < max_retries - 1:
            await_time = (attempt + 1) * 2
            print(f"   ⏳ Ждём {await_time}с перед следующей попыткой...")
            import time
            time.sleep(await_time)

    print("   ❌ Не удалось сгенерировать изображение после всех попыток")
    return None


def cleanup_image(filepath: Optional[str]) -> None:
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"   ⚠️ Не удалось удалить {filepath}: {e}")


# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    print("🔄 Загрузка статей...")
    articles = load_articles_from_sites()
    candidates = filter_articles(articles)

    if not candidates:
        print("❌ Нет подходящих новостей.")
        return

    ai_count = sum(1 for a in candidates if any(
        kw in f"{a['title']} {a['summary']}".lower()
        for kw in AI_KEYWORDS
    ))
    print(f"📊 Найдено: {len(candidates)} статей ({ai_count} про ИИ)")

    posted_count = 0
    max_posts = 1

    for art in candidates[:15]:
        if posted_count >= max_posts:
            break

        print(f"\n🔍 Обработка: {art['title'][:60]}... [{art['source']}]")

        post_text = short_summary(art["title"], art["summary"], art["link"])

        if not post_text:
            print("   ⚠️ Не удалось сгенерировать текст, пробуем следующую")
            continue

        img = generate_image(art["title"])

        try:
            if img:
                await bot.send_photo(
                    CHANNEL_ID,
                    photo=FSInputFile(img),
                    caption=post_text
                )
            else:
                await bot.send_message(CHANNEL_ID, text=post_text)

            save_posted(art["id"])
            posted_count += 1
            print(f"✅ Опубликовано: {art['source']}")

        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {e}")
        finally:
            cleanup_image(img)

    if posted_count == 0:
        print("⚠️ Не удалось опубликовать ни одного поста")
    else:
        print(f"\n🎉 Опубликовано постов: {posted_count}")


async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())










































































































