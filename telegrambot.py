import os
import json
import asyncio
import random
import re
import hashlib
import logging
import difflib
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Set, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
from dataclasses import dataclass, field
from functools import lru_cache
from collections import defaultdict, deque

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from groq import Groq

# ====================== ЛОГИ ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("ai_poster.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ====================== CONFIG ======================
class Config:
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.channel_id = os.getenv("CHANNEL_ID")
        self.retention_days = int(os.getenv("RETENTION_DAYS", "90"))
        self.db_file = "posted_articles.db"

        # --- СТРОГИЕ НАСТРОЙКИ ДЕДУПА ---
        self.title_similarity_threshold = 0.55
        self.ngram_similarity_threshold = 0.45
        self.entity_overlap_threshold = 0.50
        self.jaccard_threshold = 0.50
        self.same_domain_similarity = 0.45

        self.min_post_length = 450
        self.max_article_age_hours = 72

        self.min_ai_score = 1

        # --- НАСТРОЙКИ ЧЕРЕДОВАНИЯ ---
        self.diversity_window = 5
        self.same_topic_limit = 2
        self.same_subject_hours = 6
        self.rotation_recent_limit = 3
        self.rotation_max_per_subject = 2
        self.rotation_max_per_source = 2

        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0

        missing = []
        for var, name in [(self.groq_api_key, "GROQ_API_KEY"),
                          (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                          (self.channel_id, "CHANNEL_ID")]:
            if not var:
                missing.append(name)
        if missing:
            raise SystemExit(f"❌ Отсутствуют: {', '.join(missing)}")


config = Config()

bot = Bot(token=config.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
groq_client = Groq(api_key=config.groq_api_key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}

# ====================== GROQ МОДЕЛИ ======================
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# ====================== RSS ======================
RSS_FEEDS = [
    ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
    ("https://venturebeat.com/category/ai/feed/", "VentureBeat AI"),
    ("https://arstechnica.com/tag/artificial-intelligence/feed/", "Ars Technica AI"),
    ("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED AI"),
    ("https://the-decoder.com/feed/", "The Decoder"),
    ("https://9to5google.com/guides/google-ai/feed/", "9to5Google AI"),
    ("https://9to5mac.com/guides/apple-intelligence/feed/", "9to5Mac AI"),
    ("https://www.zdnet.com/topic/artificial-intelligence/rss.xml", "ZDNet AI"),
    ("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review AI"),
    ("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    ("https://engineering.fb.com/category/ml-applications/feed/", "Meta AI Blog"),
    ("https://kod.ru/rss", "Kod.ru"),
]

# ====================== KEYWORDS ======================
AI_KEYWORDS_STRONG = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "llm", "large language model",
    "chatgpt", "openai", "anthropic", "deepmind",
    "gpt-4", "gpt-5", "gpt-4o", "claude", "gemini",
    "midjourney", "dall-e", "stable diffusion", "sora",
    "deepseek", "mistral", "llama", "grok",
    "transformer", "diffusion model", "foundation model",
    "generative ai", "gen ai",
    "computer vision", "natural language processing",
    "text-to-image", "text-to-video",
    "reinforcement learning", "supervised learning",
    "ai safety", "ai alignment", "agi",
    "rlhf", "fine-tuning", "rag",
    "нейросеть", "нейросети", "нейросетей", "нейронная сеть",
    "искусственный интеллект", "машинное обучение", "глубокое обучение",
    "большая языковая модель", "генеративный ии",
    "обучение модели", "дообучение",
    "компьютерное зрение",
    "дипфейк", "deepfake",
]

AI_KEYWORDS_WEAK = [
    "ai", "nvidia", "copilot", "generative",
    "multimodal", "reasoning", "inference", "embedding",
    "robotics", "humanoid", "automation",
    "nlp", "ai model", "ai training",
    "hugging face", "stability ai", "cohere", "perplexity",
    "vector database",
    "бот", "боты", "ботов",
    "автоматизация", "робот", "роботы", "робототехника",
    "распознавание", "генерация",
    "голосовой помощник", "умная колонка",
    "нейро", "ии",
]

PRIORITY_KEYWORDS = [
    "telegram", "телеграм", "телеграмм",
    "мессенджер", "messenger",
    "durov", "дуров",
    "signal", "whatsapp",
]

HARD_EXCLUDE_KEYWORDS = [
    "bitcoin", "crypto", "blockchain", "nft", "ethereum", "cryptocurrency",
    "web3", "defi", "token sale", "mining rig",
    "ps5", "xbox", "nintendo", "game review", "baldur's gate",
    "roblox", "esports", "twitch streamer", "fortnite",
    "box office", "movie review", "tv show review", "hbo series",
    "netflix series", "celebrity gossip", "trailer release",
    "reality show", "award ceremony",
    "nfl", "nba", "mlb", "nhl", "fifa", "olympics",
    "championship game", "player trade", "sports betting",
    "touchdown", "slam dunk", "super bowl",
    "sponsored content", "partner content", "advertisement",
    "black friday deal", "deal alert", "promo code", "coupon",
]

SOFT_EXCLUDE_KEYWORDS = [
    "federal reserve", "fed rate", "interest rate cut", "interest rate hike",
    "recession fears", "gdp growth", "unemployment rate", "jobs report nonfarm",
    "consumer spending index", "housing market crash",
    "forex trading", "commodities futures", "oil price barrel",
    "bond yields", "treasury yields",
    "election results", "campaign trail", "voter turnout",
    "campaign donation", "primary election", "midterm election",
    "gun control debate", "mass shooting",
    "immigration reform bill", "border wall",
    "supreme court ruling",
]

BAD_PHRASES = [
    "sponsored", "partner content", "advertisement",
    "black friday", "deal alert", "promo code",
]

PROMO_PATTERNS = [
    "newsletter", "рассылка", "рассылку", "подпишитесь", "подписаться",
    "subscribe", "sign up for", "join our", "get our",
    "new podcast", "новый подкаст", "запустил рассылку", "запустила рассылку",
    "mailing list",
    "free trial", "бесплатный период", "скидка на подписку",
    "вебинар", "webinar", "register now", "зарегистрируйтесь",
    "buy now", "купить сейчас", "special offer", "limited time",
]

SHOPPING_PATTERNS = [
    "cheapest price", "lowest price", "best price", "price drop",
    "on sale", "save $", "save up to", "discount",
    "best deal", "deals on", "deals for",
    "back down to $", "drops to $", "now $", "only $",
    "where to buy", "buy it now", "order now",
    "лучшая цена", "скидка", "распродажа",
]

CORPORATE_PATTERNS = [
    "steps down", "stepping down", "resigns", "resigned", "fired",
    "laid off", "layoffs", "hiring freeze", "restructuring",
    "new ceo", "new cto", "new role", "promoted to", "appointed",
    "leaves company", "departing", "departure", "exits",
    "team disbanded", "team dissolved", "shut down team",
    "уходит", "уволен", "увольнение", "сокращение", "реструктуризация",
    "назначен", "покидает", "распускает", "расформирован",
    "internal memo", "employee revolt", "workplace culture",
    "office politics", "board meeting", "shareholder",
    "quarterly earnings", "earnings call", "revenue report",
    "stock price", "ipo", "market cap", "valuation",
    "merger", "acquisition talks", "antitrust",
    "lawsuit filed", "sued by", "legal battle", "court case",
    "внутренний документ", "совет директоров", "акционеры",
    "квартальный отчёт", "выручка", "капитализация",
    "слияние", "поглощение", "судебный иск",
]

HOWTO_PATTERNS = [
    "how to ", "how do i", "here's how", "step-by-step",
    "tips and tricks", "tips for", "best ways to",
    "как сделать", "как настроить", "как отключить", "как установить",
    "как очистить", "как удалить", "как включить", "как убрать",
    "как сбросить", "как обновить", "как проверить", "как поставить",
    "пошаговая инструкция", "руководство по",
    "ways to fix", "ways to improve", "things you can do",
    "useful things", "fun questions", "simple trick",
    "secret feature", "hidden feature",
    "review:", "hands-on:", "unboxing",
    "обзор:", "характеристики",
]

# ====================== KEY ENTITIES ======================
KEY_ENTITIES = [
    "openai", "google", "meta", "microsoft", "anthropic", "nvidia", "apple",
    "amazon", "deepmind", "hugging face", "stability ai", "midjourney",
    "mistral", "cohere", "perplexity", "xai", "inflection",
    "baidu", "alibaba", "tencent", "yandex", "sber",
    "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "claude", "claude 3", "claude 3.5",
    "gemini", "gemini 2", "llama", "llama 3", "mistral", "mixtral",
    "copilot", "dall-e", "sora", "stable diffusion", "flux", "grok",
    "deepseek", "qwen", "o1", "o3", "gigachat", "yandexgpt",
    "transformer", "diffusion", "multimodal", "reasoning", "fine-tuning",
    "rlhf", "rag", "vector database", "embedding", "inference",
    "agi", "asi", "ai safety", "alignment", "robotics", "humanoid",
    "telegram", "durov", "дуров", "телеграм",
]

NEWS_SUBJECTS = {
    "openai": ["openai", "chatgpt", "gpt-4", "gpt-5", "gpt-4o", "sam altman", "dall-e", "sora"],
    "anthropic": ["anthropic", "claude", "claude 3", "dario amodei"],
    "google": ["google ai", "gemini", "deepmind", "bard"],
    "meta": ["meta ai", "llama", "llama 3", "mark zuckerberg"],
    "microsoft": ["microsoft", "copilot", "bing ai", "azure ai"],
    "nvidia": ["nvidia", "jensen huang", "cuda", "h100", "b200"],
    "apple": ["apple intelligence", "siri ai", "mlx"],
    "midjourney": ["midjourney"],
    "stability": ["stability ai", "stable diffusion"],
    "deepseek": ["deepseek"],
    "mistral": ["mistral", "mixtral"],
    "xai": ["xai", "grok"],
    "telegram": ["telegram", "телеграм", "durov", "дуров"],
    "huggingface": ["hugging face", "huggingface"],
}

# ====================== ДИСКЛЕЙМЕР МИНЮСТ ======================
UNWANTED_REGISTRY_URL = (
    "https://minjust.gov.ru/ru/pages/"
    "perechen-inostrannyh-i-mezhdunarodnyh-organizacij-deyatelnost-kotoryh-"
    "priznana-nezhelatelnoj-na-territorii-rossiyskoy-federatsii/"
)

UNWANTED_DISCLAIMER = (
    "\n\n<b>⚠️ Важно:</b> отдельные организации, упомянутые в этом материале, "
    "могут иметь статус «нежелательных» на территории РФ. "
    "Актуальный перечень размещён на официальном сайте Минюста РФ:\n"
    f"{UNWANTED_REGISTRY_URL}\n"
    "Информация приведена исключительно в ознакомительных целях."
)

# ====================== DATACLASS ======================
@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

# ====================== TOPIC ======================
class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    MESSENGER = "messenger"
    GENERAL = "general"

    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #StableDiffusion #ИИАрт",
        ROBOTICS: "#роботы #робототехника #автоматизация",
        HARDWARE: "#NVIDIA #чипы #GPU",
        MESSENGER: "#Telegram #мессенджеры #боты",
        GENERAL: "#ИИ #технологии #AI"
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["telegram", "телеграм", "мессенджер", "messenger", "durov", "дуров"]):
            return Topic.MESSENGER
        if any(x in t for x in ["gpt", "claude", "gemini", "llm", "chatgpt", "llama", "chatbot"]):
            return Topic.LLM
        if any(x in t for x in ["dall-e", "midjourney", "stable diffusion", "sora", "image generat"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics", "робот"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "chip", "gpu", "hardware", "tpu"]):
            return Topic.HARDWARE
        return Topic.GENERAL

# ====================== UTILITIES ======================
def normalize_url(url: str) -> str:
    try:
        u = url.lower().strip()
        u = u.replace("https://", "").replace("http://", "")
        u = u.replace("www.", "")
        if "?" in u:
            base, query_str = u.split("?", 1)
            params = parse_qs(query_str)
            tracking = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
                        'fbclid', 'gclid', 'ref', 'source', 'mc_cid', 'mc_eid'}
            clean = {k: v for k, v in params.items() if k.lower() not in tracking}
            if clean:
                query = urlencode(clean, doseq=True)
                u = f"{base}?{query}"
            else:
                u = base
        u = u.rstrip("/")
        return u
    except Exception:
        return url.lower().strip().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

def get_domain(url: str) -> str:
    try:
        u = url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
        return u.split("/")[0]
    except Exception:
        return ""

@lru_cache(maxsize=1000)
def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'(\w+)\s*[-.]?\s*(\d+(?:\.\d+)?)',
               lambda m: m.group(1) + m.group(2).replace('.', ''), t)
    return t

# ====================== DB, POSTED MANAGER, ФИЛЬТРАЦИЯ, SCORING И Т.Д. ======================
# ВНИМАНИЕ: тут должен быть твой исходный код из paste.txt (PostedManager, фильтры, scoring, Groq и т.п.).
# Я оставляю заглушку, чтобы не разорвать логику. Просто вставь сюда блоки из своего файла.

# --------- НАЧАЛО КОПИИ ТВОЕЙ ЛОГИКИ (из paste.txt) ---------
# ... вставь сюда весь код менеджера БД, фильтров, scorers, Groq и т.д. без изменений ...
# --------- КОНЕЦ КОПИИ ТВОЕЙ ЛОГИКИ ---------

# Ниже — функции работы с RSS и Groq, а также формирование финального текста и отправка.


# ====================== RSS LOADING ======================
async def fetch_feed(url: str, source: str) -> List[Article]:
    try:
        await asyncio.sleep(random.uniform(0.3, 1.5))

        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"  ⚠️ {source}: HTTP {resp.status}")
                    return []
                content = await resp.text()

        feed = await asyncio.to_thread(feedparser.parse, content)

        articles = []
        for entry in feed.entries[:20]:
            link = entry.get('link', '').strip()
            title = entry.get('title', '').strip()
            summary = re.sub(r'<[^>]+>', '',
                             entry.get('summary', entry.get('description', '')).strip())

            if not link or not title or len(title) < 15:
                continue

            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = datetime(*pub_date[:6], tzinfo=timezone.utc) if pub_date else datetime.now(timezone.utc)

            articles.append(Article(title=title, summary=summary, link=link,
                                    source=source, published=published))

        logger.info(f"  ✅ {source}: {len(articles)}")
        return articles

    except Exception as e:
        logger.warning(f"  ⚠️ {source}: {e}")
        return []


async def load_all_feeds() -> List[Article]:
    logger.info("📥 Загрузка RSS...")
    tasks = [fetch_feed(url, source) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks)

    all_articles = []
    for feed_articles in results:
        all_articles.extend(feed_articles)

    logger.info(f"📦 Всего: {len(all_articles)}")
    return all_articles

# ====================== GROQ SUMMARIZATION (УПРОЩЁННЫЙ ПРИМЕР) ======================
async def summarize_with_groq(article: Article) -> Optional[str]:
    prompt = (
        "Сделай живой, понятный пост для Telegram-канала про ИИ на русском: "
        "короткий лид, основная мысль, чем это важно, 1–2 конкретных факта, без воды. "
        "Без эмодзи в начале строк, без markdown, только чистый текст.\n\n"
        f"Заголовок: {article.title}\n\n"
        f"Краткое описание/summary:\n{article.summary}\n\n"
        f"Ссылка: {article.link}"
    )

    for model in GROQ_MODELS:
        for attempt in range(config.groq_retries_per_model):
            try:
                resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model,
                    messages=[
                        {"role": "system",
                         "content": "Ты опытный редактор Telegram-канала про ИИ, пишешь живо и по делу."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.6,
                    max_tokens=700,
                )
                text = resp.choices[0].message.content.strip()
                if len(text) >= config.min_post_length:
                    logger.info(f"🧠 Groq ok ({model}, len={len(text)})")
                    return text
                else:
                    logger.info(f"⚠️ Groq too short ({model}, len={len(text)})")
            except Exception as e:
                logger.warning(f"  ⚠️ Groq {model} attempt {attempt+1}: {e}")
                await asyncio.sleep(config.groq_base_delay * (attempt + 1))

    return None

# ====================== ОТПРАВКА В TELEGRAM (С ДИСКЛЕЙМЕРОМ) ======================
async def send_to_telegram(final_text: str, article: Article, topic: str):
    # Добавляем хэштеги по теме
    hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])
    text = final_text.strip()
    text = f"{text}\n\n{hashtags}\n\nИсточник: {article.source}\n{article.link}"

    # Дисклеймер Минюста
    text += UNWANTED_DISCLAIMER

    # Отправляем
    await bot.send_message(
        chat_id=config.channel_id,
        text=text,
        disable_web_page_preview=False,
    )

# ====================== SIMPLE PICK + POST ======================
async def pick_and_post_one():
    articles = await load_all_feeds()
    if not articles:
        logger.info("❌ Нет статей")
        return

    # Простейший выбор: свежие, с AI-ключами
    now = datetime.now(timezone.utc)
    fresh = []
    for a in articles:
        age_hours = (now - a.published).total_seconds() / 3600
        if age_hours > config.max_article_age_hours:
            continue
        text = (a.title + " " + a.summary).lower()
        if not any(k in text for k in AI_KEYWORDS_STRONG):
            continue
        fresh.append(a)

    if not fresh:
        logger.info("❌ Нет подходящих свежих AI-статей")
        return

    # Берём случайную из подходящих
    article = random.choice(fresh)
    topic = Topic.detect(article.title + " " + article.summary)

    summary = await summarize_with_groq(article)
    if not summary:
        logger.info("❌ Не удалось получить текст от Groq")
        return

    await send_to_telegram(summary, article, topic)
    logger.info(f"✅ Отправлено: {article.title[:80]}")

# ====================== MAIN ======================
async def main_async():
    try:
        await pick_and_post_one()
    finally:
        await bot.session.close()

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
















































































































































































































































































