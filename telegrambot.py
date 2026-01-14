import os
import json
import asyncio
import random
import re
import time
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
LAST_TYPE_FILE = "last_post_type.json"

REACTIONS_TEXT = (
    "Если формат зашел — жми 👍\n"
    "Не согласен — выбери 😡\n"
    "Хочешь продолжение — поставь 🔥\n"
    "Конфиг рабочий? жми 🟢, лагает — тыкай 🔴\n"
    "Протокол топ? ставь 🚀, если фейл — жми 💥\n"
    "Юзаешь? отмечай 😎, если нет — выбирай 🤔"
)

# ============ СТИЛИ ПОСТОВ ============

POST_STYLES = [
    {
        "name": "восторженный_гик",
        "intro": "Ты ведёшь новостной канал про ИИ и технологии. Делишься находками и новыми разработками, без рекламного пафоса.",
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
        "tone": "Деловой и конкретный. Без пафоса, минимум оценок.",
        "emojis": "⚙️✅📱🔧💪"
    },
    {
        "name": "футурист",
        "intro": "Ты — энтузиаст будущего ИИ. Показываешь, как новая работа, модель или устройство вписываются в картину развития технологий.",
        "tone": "Сдержанно вдохновляющий. Основной упор на факты и аккуратный взгляд вперёд.",
        "emojis": "🌟🔮🚀🌍✨"
    }
]

POST_STRUCTURES = [
    "hook_features_conclusion",
    "problem_solution",
    "straight_news"
]

# ============ КЛЮЧЕВЫЕ СЛОВА ============

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
    "электромобиль", "tesla", "электрокар", "батарея",
    "аккумулятор",
    "прорыв", "инновация", "технология"
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

# ============ АНТИРЕКЛАМНЫЙ ФИЛЬТР ============

BAD_PHRASES = [
    "предлагает решение",
    "предлагает уникальное решение",
    "обеспечивает высококачественную защиту",
    "обеспечивает надёжную защиту",
    "обеспечивает защиту",
    "позволяет сосредоточиться на своих задачах",
    "позволяет не думать об угрозах",
    "делает бизнес устойчивее",
    "позволяет бизнесу работать устойчивее",
    "значительно упрощает",
    "кардинально упрощает",
    "комплексное решение для",
    "идеальное решение для",
    "помогает бизнесу эффективнее работать",
]

def is_too_promotional(text: str) -> bool:
    low = text.lower()
    if any(p in low for p in BAD_PHRASES):
        return True
    if ("обеспечивает" in low or "позволяет" in low or "предлагает решение" in low) and \
       not any(k in low for k in ["за счёт", "за счет", "используя", "через", "например", "в том числе", "фильтрации", "анализ трафика", "rate limiting", "балансировщик"]):
        return True
    return False

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
        "llm": "#ChatGPT #LLM #нейросети",
        "image_gen": "#AI #генерация #нейросети",
        "robotics": "#роботы #технологии #безопасность",
        "space": "#космос #SpaceX #технологии",
        "hardware": "#железо #GPU #технологии",
        "ai": "#AI #нейросети #технологии",
        "tech": "#технологии #новинки #гаджеты"
    }
    return hashtag_map.get(topic, "#технологии #новости")

# ===== УЛУЧШЕННАЯ обрезка — только основной текст, по предложениям =====

def ensure_complete_sentence(text: str) -> str:
    """Убеждаемся, что текст заканчивается на завершённое предложение."""
    text = text.strip()
    if not text:
        return text
    
    # Если уже заканчивается на знак препинания — ОК
    if text[-1] in '.!?':
        return text
    
    # Ищем последний знак завершения предложения
    last_period = text.rfind('.')
    last_exclaim = text.rfind('!')
    last_question = text.rfind('?')
    
    last_end = max(last_period, last_exclaim, last_question)
    
    if last_end > 0:
        return text[:last_end + 1]
    
    # Если знаков нет — добавляем точку
    return text + '.'

def trim_core_text_to_limit(core_text: str, max_core_length: int) -> str:
    """
    Обрезает основной текст поста по предложениям, чтобы уложиться в лимит.
    Гарантирует, что текст заканчивается завершённым предложением.
    """
    core_text = core_text.strip()
    
    if len(core_text) <= max_core_length:
        return ensure_complete_sentence(core_text)
    
    # Разбиваем на предложения (сохраняя знаки препинания)
    # Паттерн: разделяем после .!? но не внутри сокращений типа "т.е.", "и т.д."
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, core_text)
    
    result = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
            
        candidate = (result + " " + sentence).strip() if result else sentence
        
        if len(candidate) <= max_core_length:
            result = candidate
        else:
            # Не влезает — останавливаемся
            break
    
    # Если ничего не влезло — берём первое предложение и обрезаем жёстко
    if not result and sentences:
        result = sentences[0][:max_core_length]
        # Обрезаем до последнего пробела, чтобы не резать слово
        if len(result) == max_core_length and ' ' in result:
            result = result.rsplit(' ', 1)[0]
    
    return ensure_complete_sentence(result)

def build_final_post(core_text: str, hashtags: str, link: str, max_total: int = 1024) -> str:
    """
    Собирает финальный пост, гарантируя что:
    1. Общая длина не превышает лимит
    2. Хештеги, реакции и ссылка всегда присутствуют
    3. Основной текст заканчивается завершённым предложением
    """
    source_line = f'\n\n🔗 <a href="{link}">Источник</a>'
    hashtag_line = f"\n\n{hashtags}"
    reactions_line = f"\n\n{REACTIONS_TEXT}"
    
    # Считаем место для служебных частей
    service_length = len(hashtag_line) + len(reactions_line) + len(source_line)
    max_core_length = max_total - service_length - 10  # запас 10 символов
    
    # Обрезаем основной текст если нужно
    trimmed_core = trim_core_text_to_limit(core_text, max_core_length)
    
    # Собираем финальный пост
    final = trimmed_core + hashtag_line + reactions_line + source_line
    
    # Финальная проверка
    if len(final) > max_total:
        # Аварийная обрезка — уменьшаем core ещё
        overflow = len(final) - max_total
        trimmed_core = trim_core_text_to_limit(core_text, max_core_length - overflow - 20)
        final = trimmed_core + hashtag_line + reactions_line + source_line
    
    return final

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

    articles.extend(load_rss(
        "https://all-rss.ru/export/55.xml",
        "Overclockers Hardware"
    ))
    articles.extend(load_rss(
        "https://all-rss.ru/export/57.xml",
        "Overclockers IT"
    ))
    articles.extend(load_rss("https://hightech.fm/feed", "Хайтек"))
    articles.extend(load_rss("https://nplus1.ru/rss", "N+1"))

    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://servernews.ru/rss", "ServerNews"))

    return articles

def filter_articles(articles: List[Dict]) -> List[Dict]:
    ai_articles = []
    tech_articles = []

    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()

        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

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

# ============ ГЕНЕРАЦИЯ ТЕКСТА ============

def build_dynamic_prompt(title: str, summary: str, style: dict, structure: str) -> str:
    news_text = f"Заголовок: {title}\n\nТекст: {summary}"

    base_instructions = f"""
{style['intro']}

Тональность: {style['tone']}
Эмодзи: {style['emojis']}
"""

    structure_instructions = {
        "hook_features_conclusion": """
Структура:
1. КРАТКОЕ СУТЬ — что за система/исследование и в чём новизна.
2. КАК РАБОТАЕТ — 2–3 конкретных механизма или приёма, за счёт чего достигается результат.
3. ВЫВОД — отдельным последним предложением: чем это полезно и что это меняет.
""",
        "problem_solution": """
Структура:
1. ПРОБЛЕМА — какую конкретную задачу решают (узкие места, нагрузка, надёжность и т.п.).
2. РЕШЕНИЕ — какие подходы используются (архитектура, форматы чисел, работа с памятью, алгоритмы и т.п.).
3. ЭФФЕКТ — отдельным последним предложением: что это даёт пользователям/разработчикам/инфраструктуре.
""",
        "straight_news": """
Структура:
1. ФАКТ — что представили/исследовали без рекламы.
2. ТЕХДЕТАЛИ — 2–3 ключевых технических особенности или приёма.
3. КОНТЕКСТ — отдельным последним предложением: зачем это и в каких сценариях особенно полезно.
"""
    }

    prompt = f"""
{base_instructions}

НОВОСТЬ:
{news_text}

{structure_instructions.get(structure, structure_instructions['straight_news'])}

ТРЕБОВАНИЯ:
• Напиши один связный абзац длиной 400–600 символов.
• Язык: только русский.
• Обязательно упомяни 2–3 конкретных технических приёма или механизма.
• Последнее предложение должно быть явным выводом.
• Текст ОБЯЗАН заканчиваться точкой, восклицательным или вопросительным знаком.
• 0–2 эмодзи, только по делу.
• Нельзя писать общие фразы без пояснения «как именно».
• Пиши по фактам из новости.

ЗАПРЕЩЕНО:
• Рекламные формулировки без технического объяснения.
• Клише: «позволяет сосредоточиться на своих задачах», «делает бизнес устойчивее».
• Продажный тон, призывы попробовать или купить.
• Обрывать текст на середине предложения.

ВЫДАЙ ТОЛЬКО ТЕКСТ ПОСТА, без хештегов и ссылок.
"""
    return prompt

def validate_generated_text(text: str) -> tuple[bool, str]:
    """
    Проверяет сгенерированный текст на корректность.
    Возвращает (is_valid, reason).
    """
    text = text.strip()
    
    if not text:
        return False, "Пустой текст"
    
    if len(text) < 100:
        return False, f"Слишком короткий текст ({len(text)} символов)"
    
    # Проверяем завершённость
    if text[-1] not in '.!?':
        return False, "Текст не заканчивается знаком препинания"
    
    # Проверяем на обрыв (незакрытые скобки, кавычки)
    if text.count('(') != text.count(')'):
        return False, "Незакрытые скобки"
    
    if text.count('«') != text.count('»'):
        return False, "Незакрытые кавычки"
    
    # Проверяем что последнее предложение не слишком короткое (признак обрыва)
    sentences = re.split(r'[.!?]', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if sentences and len(sentences[-1]) < 10:
        # Последний фрагмент слишком короткий — возможно обрыв
        # Но это может быть и нормальным коротким выводом, так что не блокируем
        pass
    
    return True, "OK"

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    style = random.choice(POST_STYLES)
    structure = random.choice(POST_STRUCTURES)

    print(f"  📝 Стиль: {style['name']}, структура: {structure}")

    prompt = build_dynamic_prompt(title, summary, style, structure)

    max_attempts = 2
    
    for attempt in range(max_attempts):
        try:
            res = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — автор новостного Telegram-канала про ИИ и технологии. "
                            "Пишешь по фактам, с упором на механизмы и подходы, без рекламного тона. "
                            "ВАЖНО: всегда заканчивай текст полным предложением с точкой, восклицательным или вопросительным знаком."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=600,
            )
            core = res.choices[0].message.content.strip()

            # Убираем внешние кавычки если есть
            if core.startswith('"') and core.endswith('"'):
                core = core[1:-1]
            if core.startswith('«') and core.endswith('»'):
                core = core[1:-1]
            
            core = core.strip()

            # Валидация
            is_valid, reason = validate_generated_text(core)
            if not is_valid:
                print(f"  ⚠️ Попытка {attempt + 1}: {reason}")
                if attempt < max_attempts - 1:
                    continue
                # Пытаемся исправить
                core = ensure_complete_sentence(core)

            # Проверка на рекламность
            if is_too_promotional(core):
                print("  ⚠️ Текст слишком рекламный по формулировкам, пропускаем")
                return None

            # Определяем тему и хештеги
            topic = detect_topic(title, summary)
            hashtags = get_hashtags(topic)

            # Собираем финальный пост с гарантией корректной длины и завершённости
            final = build_final_post(core, hashtags, link, max_total=1024)

            print(f"  ✅ Сгенерирован пост: {len(final)} символов")
            return final

        except Exception as e:
            print(f"❌ OpenAI ошибка: {e}")
            if attempt < max_attempts - 1:
                time.sleep(2)
                continue
            return None
    
    return None

# ============ ГЕНЕРАЦИЯ КАРТИНОК ============

def generate_image(title: str, max_retries: int = 3) -> Optional[str]:
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

            print(f"  🎨 Генерация изображения (попытка {attempt + 1}/{max_retries})...")

            resp = requests.get(url, timeout=90, headers=HEADERS)

            if resp.status_code == 200:
                content_type = resp.headers.get('content-type', '')
                if 'image' in content_type and len(resp.content) > 10000:
                    fname = f"img_{seed}.jpg"
                    with open(fname, "wb") as f:
                        f.write(resp.content)
                    print(f"  ✅ Изображение сохранено: {fname}")
                    return fname
                else:
                    print(f"  ⚠️ Получен неверный контент (size: {len(resp.content)})")
            else:
                print(f"  ⚠️ HTTP {resp.status_code}")

        except requests.Timeout:
            print("  ⚠️ Таймаут при генерации изображения")
        except requests.RequestException as e:
            print(f"  ⚠️ Ошибка сети: {e}")
        except Exception as e:
            print(f"  ❌ Неожиданная ошибка: {e}")

        if attempt < max_retries - 1:
            await_time = (attempt + 1) * 2
            print(f"  ⏳ Ждём {await_time}с перед следующей попыткой...")
            time.sleep(await_time)

    print("  ❌ Не удалось сгенерировать изображение после всех попыток")
    return None

def cleanup_image(filepath: Optional[str]) -> None:
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"  ⚠️ Не удалось удалить {filepath}: {e}")

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

    last_type = load_last_post_type()
    posted_count = 0
    max_posts = 1

    hardware_candidates = [c for c in candidates if c.get("post_type") == "hardware"]
    it_candidates = [c for c in candidates if c.get("post_type") == "it"]

    def pick_next_article() -> Optional[Dict]:
        nonlocal last_type
        if last_type == "hardware":
            if it_candidates:
                return it_candidates.pop(0)
            elif hardware_candidates:
                return hardware_candidates.pop(0)
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
            print("  ⚠️ Не удалось сгенерировать текст, пробуем следующую")
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



















































































































