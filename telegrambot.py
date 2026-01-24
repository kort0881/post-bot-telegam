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
    print("⚠️ WARNING: Не все ключи найдены в ENV!")

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
TELEGRAM_CAPTION_LIMIT = 1024

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

def ensure_complete_sentence(text: str) -> str:
    text = text.strip()
    if not text: return text
    if text[-1] in '.!?': return text
    last_period = text.rfind('.')
    last_exclaim = text.rfind('!')
    last_question = text.rfind('?')
    last_end = max(last_period, last_exclaim, last_question)
    if last_end > 0: return text[:last_end + 1]
    return text + '.'

def trim_core_text_to_limit(core_text: str, max_core_length: int) -> str:
    core_text = core_text.strip()
    if len(core_text) <= max_core_length:
        return ensure_complete_sentence(core_text)
    sentence_pattern = r'(?<=[.!?])\s+'
    sentences = re.split(sentence_pattern, core_text)
    result = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence: continue
        candidate = (result + " " + sentence).strip() if result else sentence
        if len(candidate) <= max_core_length:
            result = candidate
        else:
            break
    if not result and sentences:
        result = sentences[0][:max_core_length]
        if len(result) == max_core_length and ' ' in result:
            result = result.rsplit(' ', 1)[0]
    return ensure_complete_sentence(result)

def build_final_post(core_text: str, hashtags: str, link: str, max_total: int = 1024) -> str:
    cta_line = "\n\n🔥 — огонь! | 🗿 — ну такое | ⚡ — буду пользоваться"
    source_line = f'\n🔗 <a href="{link}">Источник</a>'
    hashtag_line = f"\n\n{hashtags}"
    
    service_length = len(cta_line) + len(hashtag_line) + len(source_line)
    max_core_length = max_total - service_length - 10
    
    trimmed_core = trim_core_text_to_limit(core_text, max_core_length)
    
    final = trimmed_core + cta_line + hashtag_line + source_line
    if len(final) > max_total:
        overflow = len(final) - max_total
        trimmed_core = trim_core_text_to_limit(core_text, max_core_length - overflow - 20)
        final = trimmed_core + cta_line + hashtag_line + source_line
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
    return articles

def load_articles_from_sites() -> List[Dict]:
    articles: List[Dict] = []
    # HABR
    articles.extend(load_rss("https://habr.com/ru/rss/hub/artificial_intelligence/all/?fl=ru", "Habr AI"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/machine_learning/all/?fl=ru", "Habr ML"))
    articles.extend(load_rss("https://habr.com/ru/rss/hub/neural_networks/all/?fl=ru", "Habr Neural"))
    
    # TECH
    articles.extend(load_rss("https://3dnews.ru/news/rss/", "3DNews"))
    articles.extend(load_rss("https://www.ixbt.com/export/news.rss", "iXBT"))
    articles.extend(load_rss("https://nplus1.ru/rss", "N+1"))
    articles.extend(load_rss("https://hightech.fm/feed", "Хайтек"))
    
    return articles

def filter_articles(articles: List[Dict]) -> List[Dict]:
    ai_articles = []
    tech_articles = []

    for e in articles:
        text = f"{e['title']} {e['summary']}".lower()

        # ФИЛЬТРАЦИЯ ПО СТОП-СЛОВАМ (БИРЖА, ПОЛИТИКА, СПОРТ)
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue

        source = e.get("source", "")
        if source in ["3DNews", "iXBT", "Overclockers"]:
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

# ============ ГЕНЕРАЦИЯ ТЕКСТА (ВЕСЕЛЫЙ, НО АДЕКВАТНЫЙ) ============

def build_dynamic_prompt(title: str, summary: str) -> str:
    news_text = f"Заголовок: {title}\n\nТекст: {summary}"

    prompt = f"""
Ты — остроумный техно-блогер. 
Твоя задача: Написать пост о новости, который будет легко и интересно читать.

НОВОСТЬ:
{news_text}

ТРЕБОВАНИЯ:
1. ВСТУПЛЕНИЕ: Строго обычное: "Всем привет! 👋" или "Привет, друзья! ✌️". 
   - Не используй "Йоу", "Гики", "На связи" и т.п.
2. СТИЛЬ: Живой, с легкой иронией или добрым юмором. Пиши так, как будто рассказываешь другу.
   - Используй метафоры и сравнения.
   - Можно пошутить над тем, что "Skynet уже близко" или "опять всё переизобрели", но в меру.
   - Не уходи в клоунаду, техническая суть должна быть понятна.
3. ЗАПРЕТЫ:
   - Никаких продажных фраз ("покупайте", "лучшее решение").
   - Никаких скучных канцеляризмов.
4. СТРУКТУРА:
   - Приветствие.
   - Короткий заход (хук/шутка).
   - Суть новости (что случилось и как это работает).
   - Вывод (твое мнение: круто это или нет).
5. ОБЪЕМ: до 800 знаков.
"""
    return prompt

def validate_generated_text(text: str) -> tuple[bool, str]:
    text = text.strip()
    if not text: return False, "Пустой текст"
    if len(text) < 50: return False, "Слишком короткий"
    return True, "OK"

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    prompt = build_dynamic_prompt(title, summary)
    print(f"  📝 Генерирую пост (Fun but Normal)...")

    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7, # Чуть выше креативность для шуток
            max_tokens=700,
        )
        core = res.choices[0].message.content.strip()

        if core.startswith('"') and core.endswith('"'): core = core[1:-1]
        
        if is_too_promotional(core):
            print("  ⚠️ Текст слишком рекламный, пропускаем.")
            return None

        topic = detect_topic(title, summary)
        hashtags = get_hashtags(topic)
        final = build_final_post(core, hashtags, link, max_total=TELEGRAM_CAPTION_LIMIT)
        return final

    except Exception as e:
        print(f"❌ OpenAI ошибка: {e}")
        return None

# ============ ГЕНЕРАЦИЯ КАРТИНОК ============

def generate_image(title: str, max_retries: int = 2) -> Optional[str]:
    # Делаем промпт чуть ярче для веселого стиля
    style_prompt = "futuristic concept art, vibrant colors, technology, 3d render, detailed, cyberpunk, neon"
    
    for attempt in range(max_retries):
        seed = random.randint(0, 10**7)
        clean_title = re.sub(r'[^a-zA-Z0-9]', ' ', title)[:50]
        prompt = f"{style_prompt}, {clean_title}"
        
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=1024&height=1024&nologo=true"
        
        try:
            print(f"  🎨 Генерация картинки...")
            resp = requests.get(url, timeout=40, headers=HEADERS)
            if resp.status_code == 200 and len(resp.content) > 10000:
                fname = f"img_{seed}.jpg"
                with open(fname, "wb") as f: f.write(resp.content)
                return fname
        except Exception as e:
            print(f"  ⚠️ Ошибка картинки: {e}")
    return None

def cleanup_image(filepath: Optional[str]) -> None:
    if filepath and os.path.exists(filepath):
        try: os.remove(filepath)
        except: pass

# ============ АВТОПОСТ ============

async def autopost():
    clean_old_posts()
    print("🔄 Загрузка статей...")
    articles = load_articles_from_sites()
    candidates = filter_articles(articles)

    if not candidates:
        print("❌ Нет подходящих новостей.")
        return

    print(f"📊 Найдено: {len(candidates)} статей.")
    
    last_type = load_last_post_type()
    posted_count = 0
    max_posts = 1 

    hardware_candidates = [c for c in candidates if c.get("post_type") == "hardware"]
    it_candidates = [c for c in candidates if c.get("post_type") == "it"]

    def pick_next_article() -> Optional[Dict]:
        nonlocal last_type
        if last_type == "hardware":
            if it_candidates: return it_candidates.pop(0)
            elif hardware_candidates: return hardware_candidates.pop(0)
        else:
            if hardware_candidates: return hardware_candidates.pop(0)
            elif it_candidates: return it_candidates.pop(0)
        return None

    while posted_count < max_posts:
        art = pick_next_article()
        if not art: break

        print(f"\n🔍 Обработка: {art['title']}")
        post_text = short_summary(art["title"], art["summary"], art["link"])

        if not post_text: continue

        img = generate_image(art["title"])
        
        try:
            if img:
                await bot.send_photo(CHANNEL_ID, photo=FSInputFile(img), caption=post_text)
            else:
                await bot.send_message(CHANNEL_ID, text=post_text, disable_web_page_preview=False)

            save_posted(art["id"])
            posted_count += 1
            last_type = art.get("post_type")
            save_last_post_type(last_type)
            print(f"✅ Опубликовано!")

        except Exception as e:
            print(f"❌ Ошибка отправки TG: {e}")
        finally:
            cleanup_image(img)

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())



















































































































































































































































