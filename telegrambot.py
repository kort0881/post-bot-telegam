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
LAST_TYPE_FILE = "last_post_type.json"  # сюда пишем тип последнего поста (hardware / it)

# ============ СТИЛИ ПОСТОВ (ВАРИАТИВНОСТЬ, НОВОСТИ/НАХОДКИ БЕЗ РЕКЛАМЫ) ============

POST_STYLES = [
    {
        "name": "восторженный_гик",
        "intro": "Ты ведёшь новостной канал про ИИ и технологии. Делишься интересными находками и новыми разработками, без рекламного пафоса.",
        "tone": "Энергичный, но опирающийся на факты. Короткие предложения, акцент на сути новости.",
        "emojis": "🔥🚀💡🤖✨"
    },
    {
        "name": "аналитик",
        "intro": "Ты — технический обозреватель. Объясняешь новые исследования и разработки в ИИ простыми словами, выделяешь главное.",
        "tone": "Спокойный, информативный. Факты + короткий вывод, чем примечательна эта работа или технология.",
        "emojis": "🧠📊🔬💻⚡"
    },
    {
        "name": "ироничный_обозреватель",
        "intro": "Ты — обозреватель технологий. Показываешь, что именно сделали исследователи или инженеры, иногда с лёгкой иронией.",
        "tone": "Живой, с аккуратным юмором, но без преувеличений и без маркетинговых лозунгов.",
        "emojis": "👀🎯😏🛠️💫"
    },
    {
        "name": "практик",
        "intro": "Ты — практичный специалист по ИИ. Поясняешь по сути: какая задача решается, как устроено решение и кому это может пригодиться.",
        "tone": "Деловой и конкретный. Без пафоса, минимально необходимое количество оценок.",
        "emojis": "⚙️✅📱🔧💪"
    },
    {
        "name": "футурист",
        "intro": "Ты — энтузиаст будущего ИИ. Показываешь, как новая работа, модель или устройство вписываются в общую картину развития технологий.",
        "tone": "Сдержанно вдохновляющий. Основной упор на факты и аккуратный взгляд вперёд.",
        "emojis": "🌟🔮🚀🌍✨"
    }
]

# Разные структуры постов
POST_STRUCTURES = [
    "hook_features_conclusion",  # Цепляющее начало → фишки → вывод
    "question_answer",           # Вопрос → ответ через новость
    "problem_solution",          # Проблема → как решает технология
    "surprise_details",          # Удивительный факт → подробности
    "straight_news"              # Прямая подача новости
]

# Варианты начала постов (без маркетинговых формулировок)
HOOK_TEMPLATES = [
    "Главная идея: {key_point}",
    "Коротко о сути: {key_point}",
    "Интересная деталь: {key_point}",
    "Сначала главное: {key_point}",
    "Необычный момент: {key_point}",
    "{key_point} — важный штрих к картине ИИ",
]

# ============ ПРИОРИТЕТ: ИИ И НЕЙРОСЕТИ ============

AI_KEYWORDS = [
    # Общие термины ИИ
    "нейросеть", "нейросети", "нейронная сеть", "ии", "искусственный интеллект",
    "neural network", "artificial intelligence",
    # LLM и модели
    "llm", "gpt", "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "gemini",
    "copilot", "mistral", "llama", "qwen", "gigachat", "yandexgpt",
    "kandinsky", "шедеврум", "deepseek", "grok",
    # Компании ИИ
    "openai", "anthropic", "deepmind", "сбер ai", "яндекс ai",
    "hugging face", "stability ai", "meta ai", "google ai",
    # Генеративные модели
    "stable diffusion", "midjourney", "dall-e", "sora", "runway",
    "генеративный", "генерация изображений", "генерация текста",
    "генерация видео", "text-to-image", "text-to-video",
    # ML термины
    "машинное обучение", "глубокое обучение", "transformer",
    "трансформер", "языковая модель", "мультимодальный",
    "дообучение", "обучение модели", "датасет", "fine-tuning",
    # Применения ИИ
    "чат-бот", "голосовой помощник", "автопилот", "распознавание",
    "нейросетевой", "ai-ассистент", "умный помощник",
    "компьютерное зрение", "обработка языка", "nlp",
    # Новые разработки
    "agi", "рассуждение", "агент", "ai-агент", "контекстное окно",
    "токен", "большая языковая модель", "reasoning",
    # Тренды
    "обучение с подкреплением", "rlhf", "промпт", "prompt"
]

# ============ ИНТЕРЕСНЫЕ ТЕХНОЛОГИИ ============

TECH_KEYWORDS = [
    # Анонсы и новинки
    "представил", "анонсировал", "выпустил", "релиз", "запустил",
    "новинка", "дебют", "презентация", "показал", "unveiled",
    # Гаджеты и устройства
    "смартфон", "ноутбук", "гаджет", "девайс", "устройство",
    "носимая электроника", "умные часы", "наушники",
    # Роботы и автоматизация
    "робот", "робототехника", "дрон", "беспилотник", "автопилот",
    "автономный", "boston dynamics", "tesla bot",
    # Передовые технологии
    "квантовый", "квантовый компьютер", "процессор", "чип",
    "gpu", "видеокарта", "nvidia", "amd", "intel", "apple m",
    # Космос
    "spacex", "starship", "космос", "ракета", "спутник",
    "starlink", "nasa", "роскосмос",
    # VR/AR
    "виртуальная реальность", "дополненная реальность",
    "vr", "ar", "meta quest", "apple vision",
    # Электромобили
    "электромобиль", "tesla", "электрокар", "батарея",
    "аккумулятор",
    # Будущее
    "прорыв", "инновация", "технология"
]

# ============ ИСКЛЮЧИТЬ ============

EXCLUDE_KEYWORDS = [
    # === ЭКОНОМИКА И ФИНАНСЫ ===
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

    # === БИЗНЕС-НОВОСТИ ===
    "назначен", "назначение", "отставка", "уволен",
    "генеральный директор", "ceo", "основатель ушёл",
    "сокращение штата", "увольнения", "сокращения",
    "офис", "штаб-квартира", "переезд компании",

    # === СПОРТ ===
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч",
    "олимпиада", "чемпионат", "турнир", "сборная",

    # === ИГРЫ ===
    "игра", "геймплей", "playstation", "xbox", "steam", "nintendo",
    "видеоигра", "консоль", "gaming",

    # === РАЗВЛЕЧЕНИЯ ===
    "кино", "фильм", "сериал", "музыка", "концерт", "актёр", "актер",
    "премьера", "трейлер", "netflix", "кинотеатр",

    # === ПОЛИТИКА ===
    "выборы", "президент", "парламент", "политик", "депутат",
    "санкции", "правительство", "министр", "закон", "законопроект",

    # === МЕДИЦИНА ===
    "болезнь", "covid", "пандемия", "грипп", "вакцина",

    # === КРИПТО ===
    "крипто", "bitcoin", "биткойн", "биткоин", "ethereum",
    "nft", "блокчейн", "криптовалюта", "майнинг",

    # === КРИМИНАЛ ===
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


def load_last_post_type() -> Optional[str]:
    if not os.path.exists(LAST_TYPE_FILE):
        return None
    try:
        with open(LAST_TYPE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("type")
    except Exception:
        return None


def save_last_post_type(post_type: str) -> None:
    try:
        with open(LAST_TYPE_FILE, "w", encoding="utf-8") as f:
            json.dump({"type": post_type}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ============ HELPERS ============

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())


def detect_topic(title: str, summary: str) -> str:
    """Определяет тему новости для выбора хештегов."""
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
    """Возвращает релевантные хештеги по теме."""
    hashtag_map = {
        "llm": "#ChatGPT #LLM #нейросети",
        "image_gen": "#AI #генерация #нейросети",
        "robotics": "#роботы #технологии #будущее",
        "space": "#космос #SpaceX #технологии",
        "hardware": "#железо #GPU #технологии",
        "ai": "#AI #нейросети #технологии",
        "tech": "#технологии #новинки #гаджеты"
    }
    return hashtag_map.get(topic, "#технологии #новости")


# ============ PARSERS ============

def load_rss(url: str, source: str) -> List[Dict]:
    """Загружает RSS с обработкой ошибок."""
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
    """Загружает статьи с русскоязычных источников."""
    articles: List[Dict] = []

    # === ПРИОРИТЕТ 1: ИИ и нейросети (Habr) ===
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

    # === ПРИОРИТЕТ 2: Технологии ===
    # Overclockers вместо Tproger
    articles.extend(load_rss(
        "https://all-rss.ru/export/55.xml",  # Overclockers.ru / Новости Hardware
        "Overclockers Hardware"
    ))
    articles.extend(load_rss(
        "https://all-rss.ru/export/57.xml",  # Overclockers.ru / Новости IT-рынка
        "Overclockers IT"
    ))
    articles.extend(load_rss("https://hightech.fm/feed", "Хайтек"))
    articles.extend(load_rss("https://nplus1.ru/rss", "N+1"))

    # === ПРИОРИТЕТ 3: Железо и гаджеты ===
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://servernews.ru/rss", "ServerNews"))

    return articles


# ============ ФИЛЬТРАЦИЯ ============

def filter_articles(articles: List[Dict]) -> List[Dict]:
    """Фильтрует статьи с приоритетом ИИ и помечает тип поста (hardware / it)."""
    ai_articles = []
    tech_articles = []

    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()

        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

        # Тип источника для чередования
        source = e.get("source", "")
        if source in ["Overclockers Hardware", "Overclockers IT", "3DNews", "iXBT", "ServerNews"]:
            e["post_type"] = "hardware"
        else:
            e["post_type"] = "it"

        if any(kw in text for kw in AI_KEYWORDS):
            ai_articles.append(e)
        elif any(kw in text for kw in TECH_KEYWORDS):
            tech_articles.append(e)

    ai_articles.sort(key=lambda x: x["published_parsed"], reverse=True)
    tech_articles.sort(key=lambda x: x["published_parsed"], reverse=True)

    return ai_articles + tech_articles


# ============ УЛУЧШЕННАЯ ГЕНЕРАЦИЯ ТЕКСТА ============

def build_dynamic_prompt(title: str, summary: str, style: dict, structure: str) -> str:
    """Строит динамический промпт с учётом стиля и структуры."""

    news_text = f"Заголовок: {title}\n\nТекст: {summary}"

    # Базовые инструкции
    base_instructions = f"""
{style['intro']}

Тональность: {style['tone']}
Эмодзи для использования: {style['emojis']}
"""

    # Инструкции по структуре
    structure_instructions = {
        "hook_features_conclusion": """
Структура поста:
1. ЗАХВАТ (1 предложение) — самое интересное или новое из новости.
2. СУТЬ (2–3 предложения) — что именно сделали/представили, ключевые факты и особенности.
3. ЗНАЧЕНИЕ (1 предложение) — какой вклад это даёт в развитие ИИ или технологий.
""",
        "question_answer": """
Структура поста:
1. ВОПРОС — короткий вопрос по сути новости.
2. ОТВЕТ — как именно новость на него отвечает.
3. ДЕТАЛИ — 2–3 конкретные особенности или результаты.
""",
        "problem_solution": """
Структура поста:
1. ПРОБЛЕМА — какую задачу или ограничение решает технология (1 предложение).
2. РЕШЕНИЕ — как именно это делается (2–3 предложения).
3. РЕЗУЛЬТАТ — какой эффект или польза получаются.
""",
        "surprise_details": """
Структура поста:
1. УДИВИТЕЛЬНЫЙ ФАКТ — начни с самой необычной детали из новости.
2. КОНТЕКСТ — что это означает и почему это важно.
3. ПОДРОБНОСТИ — ключевые технические моменты или условия.
""",
        "straight_news": """
Структура поста:
1. ГЛАВНОЕ — что произошло (1 предложение, без лозунгов).
2. ПОДРОБНОСТИ — ключевые факты (2–3 предложения).
3. ИТОГ — спокойный вывод, чем это интересно в контексте ИИ/технологий.
"""
    }

    prompt = f"""
{base_instructions}

НОВОСТЬ ДЛЯ ОБРАБОТКИ:
{news_text}

{structure_instructions.get(structure, structure_instructions['straight_news'])}

ЖЁСТКИЕ ТРЕБОВАНИЯ:
• Длина: 350–420 символов (не больше!).
• Язык: ТОЛЬКО русский.
• 1–2 эмодзи из списка выше — органично вплетены в текст.
• Пиши в новостном стиле: по фактам, без рекламных призывов.
• Каждое предложение должно нести смысл.
• Не начинай с «Итак», «Ну что», «Друзья».

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
• Рекламные и продающие фразы: «обязательно попробуйте», «не упустите», «лучшее решение», «идеальный инструмент» и т.п.
• Клише: «мир не стоит на месте», «будущее уже здесь», «технологии развиваются».
• Водянистые фразы: «стоит отметить», «важно понимать», «нельзя не заметить».
• Выдумывать факты, которых нет в исходнике.
• Общие слова без конкретики.

ВЫДАЙ ТОЛЬКО ТЕКСТ ПОСТА, без хештегов и ссылок.
"""
    return prompt


def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    """Генерирует пост с вариативным стилем."""

    # Выбираем случайный стиль и структуру
    style = random.choice(POST_STYLES)
    structure = random.choice(POST_STRUCTURES)

    print(f" 📝 Стиль: {style['name']}, структура: {structure}")

    prompt = build_dynamic_prompt(title, summary, style, structure)

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — автор новостного Telegram-канала про ИИ и технологии. "
                        "Пишешь живо и по делу, но без маркетингового и продающего тона."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,  # Чуть больше креативности
            max_tokens=500,
        )
        core = res.choices[0].message.content.strip()

        # Убираем кавычки если GPT обернул текст
        if core.startswith('"') and core.endswith('"'):
            core = core[1:-1]
        if core.startswith('«') and core.endswith('»'):
            core = core[1:-1]

        # Добавляем хештеги по теме
        topic = detect_topic(title, summary)
        hashtags = get_hashtags(topic)

        # Финальная сборка поста (промо оставляем)
        source_line = f"\n\n🔗 <a href=\"{link}\">Источник</a>"
        hashtag_line = f"\n\n{hashtags}"
        promo = "\n\n💥 Кто за ключами 👉 https://t.me/+EdEfIkn83Wg3ЗTE6"

        return core + hashtag_line + source_line + promo

    except Exception as e:
        print(f"❌ OpenAI ошибка: {e}")
        return None


# ============ УЛУЧШЕННАЯ ГЕНЕРАЦИЯ КАРТИНОК ============

def generate_image(title: str, max_retries: int = 3) -> Optional[str]:
    """Генерирует картинку с повторными попытками."""

    # Разные стили для разнообразия
    image_styles = [
        "futuristic minimalist illustration, soft gradients, ",
        "abstract tech visualization, geometric shapes, ",
        "modern digital art, clean lines, ",
        "sci-fi concept art, atmospheric lighting, ",
        "sleek technology render, professional, "
    ]

    style = random.choice(image_styles)

    for attempt in range(max_retries):
        seed = random.randint(0, 10**7)

        # Очищаем заголовок от проблемных символов
        clean_title = title[:60].replace('"', '').replace("'", "").replace('\n', ' ')

        prompt = (
            f"{style}{clean_title}, "
            "neural networks, innovation, technology, "
            "4k quality, no text, no letters, no words, "
            "clean composition, professional"
        )

        try:
            encoded = urllib.parse.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024&nologo=true"

            print(f" 🎨 Генерация изображения (попытка {attempt + 1}/{max_retries})...")

            resp = requests.get(url, timeout=90, headers=HEADERS)

            if resp.status_code == 200:
                # Проверяем что это действительно изображение
                content_type = resp.headers.get('content-type', '')
                if 'image' in content_type and len(resp.content) > 10000:
                    fname = f"img_{seed}.jpg"
                    with open(fname, "wb") as f:
                        f.write(resp.content)
                    print(f" ✅ Изображение сохранено: {fname}")
                    return fname
                else:
                    print(f" ⚠️ Получен неверный контент (size: {len(resp.content)})")
            else:
                print(f" ⚠️ HTTP {resp.status_code}")

        except requests.Timeout:
            print(f" ⚠️ Таймаут при генерации изображения")
        except requests.RequestException as e:
            print(f" ⚠️ Ошибка сети: {e}")
        except Exception as e:
            print(f" ❌ Неожиданная ошибка: {e}")

        # Пауза между попытками
        if attempt < max_retries - 1:
            await_time = (attempt + 1) * 2
            print(f" ⏳ Ждём {await_time}с перед следующей попыткой...")
            import time
            time.sleep(await_time)

    print(" ❌ Не удалось сгенерировать изображение после всех попыток")
    return None


def cleanup_image(filepath: Optional[str]) -> None:
    """Безопасно удаляет файл изображения."""
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f" ⚠️ Не удалось удалить {filepath}: {e}")


# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    print("🔄 Загрузка статей...")
    articles = load_articles_from_sites()
    candidates = filter_articles(articles)

    if not candidates:
        print("❌ Нет подходящих новостей про ИИ/технологии.")
        return

    ai_count = sum(1 for a in candidates if any(
        kw in f"{a['title']} {a['summary']}".lower()
        for kw in AI_KEYWORDS
    ))
    print(f"📊 Найдено: {len(candidates)} статей ({ai_count} про ИИ)")

    last_type = load_last_post_type()  # "hardware" / "it" / None

    posted_count = 0
    max_posts = 1  # Сколько постов за запуск

    # Разбиваем кандидатов по типам
    hardware_candidates = [c for c in candidates if c.get("post_type") == "hardware"]
    it_candidates = [c for c in candidates if c.get("post_type") == "it"]

    def pick_next_article() -> Optional[Dict]:
        nonlocal last_type

        # Если в прошлый раз было "hardware" — сейчас пытаемся взять "it"
        if last_type == "hardware":
            if it_candidates:
                return it_candidates.pop(0)
            elif hardware_candidates:
                return hardware_candidates.pop(0)
        # Если было "it" или None — сейчас пробуем "hardware"
        else:
            if hardware_candidates:
                return hardware_candidates.pop(0)
            elif it_candidates:
                return it_candidates.pop(0)
        return None

    while posted_count < max_posts:
        art = pick_next_article()
        if not art:
            break

        print(f"\n🔍 Обработка: {art['title'][:60]}... [{art['source']}] (type={art.get('post_type')})")

        post_text = short_summary(art["title"], art["summary"], art["link"])

        if not post_text:
            print(" ⚠️ Не удалось сгенерировать текст, пробуем следующую")
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
            last_type = art.get("post_type")
            save_last_post_type(last_type)
            print(f"✅ Опубликовано: {art['source']} (type={last_type})")

        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {e}")
        finally:
            cleanup_image(img)

    if posted_count == 0:
        print("⚠️ Не удалось опубликовать ни одного поста")
    else:
        print(f"\n🎉 Успешно опубликовано постов: {posted_count}")


async def main():
    try:
        await autopost()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())















































































































