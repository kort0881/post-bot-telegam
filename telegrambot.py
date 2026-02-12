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
from datetime import datetime, timezone
from typing import List, Set, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
from dataclasses import dataclass, field
from functools import lru_cache

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

        self.title_similarity_threshold = 0.55
        self.ngram_similarity_threshold = 0.40
        self.entity_overlap_threshold = 0.45
        self.jaccard_threshold = 0.50
        self.same_domain_similarity = 0.40

        self.min_post_length = 450
        self.max_article_age_hours = 72
        self.min_ai_score = 2

        self.diversity_window = 7
        self.same_topic_limit = 2
        self.same_subject_hours = 24

        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0

        self.rejected_retention_days = 7

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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


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
    ("https://www.unite.ai/feed/", "Unite.AI"),
    ("https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "The Verge AI"),
    ("https://9to5google.com/guides/google-ai/feed/", "9to5Google AI"),
    ("https://9to5mac.com/guides/apple-intelligence/feed/", "9to5Mac AI"),
    ("https://www.zdnet.com/topic/artificial-intelligence/rss.xml", "ZDNet AI"),
    ("https://www.cnet.com/rss/ai/", "CNET AI"),
    ("https://www.engadget.com/ai/rss.xml", "Engadget AI"),
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
    "review:", "hands-on:", "unboxing",
    "лучшая цена", "скидка", "распродажа", "купить",
    "обзор:", "характеристики",
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
    "google": ["google", "gemini", "deepmind", "bard", "google ai"],
    "meta": ["meta", "llama", "llama 3", "mark zuckerberg", "facebook ai"],
    "microsoft": ["microsoft", "copilot", "bing ai", "azure ai"],
    "nvidia": ["nvidia", "jensen huang", "cuda", "gpu", "h100", "b200"],
    "apple": ["apple", "apple intelligence", "siri", "mlx"],
    "midjourney": ["midjourney"],
    "stability": ["stability ai", "stable diffusion"],
    "deepseek": ["deepseek"],
    "mistral": ["mistral", "mixtral"],
    "xai": ["xai", "grok", "elon musk ai"],
    "telegram": ["telegram", "телеграм", "durov", "дуров"],
    "huggingface": ["hugging face", "huggingface"],
}


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


@lru_cache(maxsize=1000)
def get_title_words(title: str) -> frozenset:
    words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ0-9]+\b', title.lower())
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'to', 'of',
        'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
        'during', 'before', 'after', 'above', 'below', 'between', 'under',
        'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
        'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
        'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too',
        'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until', 'while',
        'about', 'against', 'its', 'new', 'says', 'said', 'get', 'got', 'gets',
        'make', 'made', 'makes', 'now', 'also', 'first', 'using', 'used', 'use',
        'out', 'up', 'what', 'which', 'who', 'this', 'that', 'these', 'those',
        'it', 'its', 'you', 'your', 'we', 'our', 'they', 'their', 'he', 'she',
        'him', 'her', 'his', 'hers', 'my', 'mine', 'yours', 'ours', 'theirs',
        'и', 'в', 'на', 'с', 'по', 'для', 'от', 'из', 'за', 'до', 'не',
        'что', 'как', 'это', 'все', 'его', 'она', 'они', 'мы', 'вы', 'он',
        'но', 'то', 'так', 'уже', 'или', 'ещё', 'еще', 'при', 'без',
        'тоже', 'также', 'будет', 'была', 'были', 'быть', 'может',
        'этот', 'эта', 'эти', 'тот', 'того', 'этого', 'свой', 'свои',
    }
    return frozenset(w for w in words if len(w) > 2 and w not in stop_words)


def get_sorted_word_signature(title: str) -> str:
    words = get_title_words(title)
    return ' '.join(sorted(words))


def calculate_similarity(str1: str, str2: str) -> float:
    return difflib.SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)


def ngram_similarity(str1: str, str2: str, n: int = 2) -> float:
    def get_ngrams(text: str, n: int) -> Set[str]:
        words = text.lower().split()
        if len(words) < n:
            return set(words)
        return set(' '.join(words[i:i + n]) for i in range(len(words) - n + 1))
    ng1 = get_ngrams(str1, n)
    ng2 = get_ngrams(str2, n)
    if not ng1 or not ng2:
        return 0.0
    return len(ng1 & ng2) / len(ng1 | ng2)


def extract_entities(text: str) -> Set[str]:
    text_normalized = normalize_title(text)
    found = set()
    for entity in KEY_ENTITIES:
        entity_normalized = normalize_title(entity)
        if entity_normalized in text_normalized:
            found.add(entity_normalized)
    return found


def get_content_hash(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())[:300]
    return hashlib.md5(normalized.encode()).hexdigest()


def detect_subject(text: str) -> str:
    text_lower = text.lower()
    best_subject = "other"
    best_count = 0
    for subject, keywords in NEWS_SUBJECTS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > best_count:
            best_count = count
            best_subject = subject
    return best_subject if best_count > 0 else "other"


# ====================== AI STRENGTH SCORE ======================
def ai_relevance_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for kw in AI_KEYWORDS_STRONG:
        if kw in text_lower:
            score += 2
    for kw in AI_KEYWORDS_WEAK:
        if kw in text_lower:
            score += 1
    return score


def priority_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for kw in PRIORITY_KEYWORDS:
        if kw in text_lower:
            score += 3
    return score


# ====================== ФИЛЬТРЫ ======================
def is_promo_content(text: str) -> bool:
    text_lower = text.lower()
    promo_count = sum(1 for p in PROMO_PATTERNS if p in text_lower)
    if promo_count >= 2:
        return True
    promo_title_patterns = [
        "запустил рассылку", "запустила рассылку", "новая рассылка",
        "new newsletter", "launches newsletter", "sign up",
        "подпишитесь на", "subscribe to our",
    ]
    for pattern in promo_title_patterns:
        if pattern in text_lower:
            return True
    return False


def is_shopping_content(text: str) -> bool:
    text_lower = text.lower()
    if ai_relevance_score(text_lower) >= 4:
        return False
    shopping_count = sum(1 for p in SHOPPING_PATTERNS if p in text_lower)
    if shopping_count >= 1:
        return True
    price_pattern = re.search(r'\$\d+', text_lower)
    if price_pattern:
        investment_words = ["funding", "raises", "round", "valuation", "billion",
                           "million", "investment", "инвестиции", "раунд", "оценка"]
        if not any(w in text_lower for w in investment_words):
            product_words = ["price", "buy", "sale", "deal", "discount", "cheap",
                           "review", "specs", "earbuds", "phone", "laptop", "tablet",
                           "watch", "headphone", "speaker", "camera", "monitor",
                           "цена", "купить", "обзор", "характеристики",
                           "наушники", "телефон", "ноутбук", "планшет"]
            if any(w in text_lower for w in product_words):
                return True
    return False


def is_corporate_news(text: str) -> bool:
    text_lower = text.lower()

    product_markers = [
        "launch", "release", "announce", "new model", "new feature",
        "update", "upgrade", "api", "open source", "benchmark",
        "demo", "preview", "beta", "available now", "rolls out",
        "introduces", "unveils", "reveals", "ships",
        "запуск", "релиз", "обновление", "новая версия", "доступен",
        "новая функция", "новая модель", "представил", "показал",
        "выпустил", "анонсировал",
    ]
    product_count = sum(1 for m in product_markers if m in text_lower)

    corporate_count = sum(1 for p in CORPORATE_PATTERNS if p in text_lower)

    if corporate_count >= 2 and corporate_count > product_count:
        return True

    if corporate_count >= 1 and product_count == 0:
        title_corporate = [
            "steps down", "resigns", "fired", "laid off", "new ceo",
            "departing", "leaves", "disbanded", "dissolved", "shut down",
            "уходит", "уволен", "распускает", "покидает", "назначен",
            "restructur", "реструктуризация", "сокращение",
        ]
        for tc in title_corporate:
            if tc in text_lower[:200]:
                return True

    return False


def is_economics_news(text: str) -> bool:
    text_lower = text.lower()
    if ai_relevance_score(text_lower) >= 4:
        return False
    econ_keywords = [
        "inflation rate", "interest rate", "federal reserve", "fed rate",
        "recession", "gdp", "unemployment rate", "jobs report",
        "economic growth", "tariff war", "trade deficit",
        "stock market crash", "bond yields", "treasury",
        "fiscal policy", "monetary policy", "budget deficit",
        "central bank", "forex", "commodities", "oil price",
        "consumer spending", "retail sales", "housing market",
    ]
    econ_count = sum(1 for kw in econ_keywords if kw in text_lower)
    if econ_count >= 2:
        return True
    return False


def is_local_us_news(text: str) -> bool:
    text_lower = text.lower()
    ai_policy_phrases = [
        "ai regulation", "ai safety", "ai policy", "ai executive order",
        "ai legislation", "artificial intelligence act", "ai governance",
        "ai oversight", "ai bill", "regulate ai", "ai standards",
        "ai ethics", "responsible ai", "ai framework", "ai law",
        "ai ban", "ai restrict", "tech regulation", "tech policy",
    ]
    if any(phrase in text_lower for phrase in ai_policy_phrases):
        return False
    if ai_relevance_score(text_lower) >= 4:
        return False
    global_markers = [
        "global", "worldwide", "international", "launch", "release",
        "announce", "research", "open source", "api", "platform",
        "model", "technology", "startup", "funding",
    ]
    if any(marker in text_lower for marker in global_markers):
        return False
    us_internal = [
        "fbi investigation", "cia operation", "homeland security alert",
        "us military operation", "border patrol", "ice raid",
        "state legislature", "city council vote", "local police",
        "district attorney", "county sheriff", "school board",
        "voter registration", "ballot measure", "gerrymandering",
        "town hall meeting", "state governor",
    ]
    for kw in us_internal:
        if kw in text_lower:
            return True
    return False


def is_relevant(article: Article) -> bool:
    text = f"{article.title} {article.summary}".lower()

    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        logger.info(f"  ⏰ TOO_OLD ({age_hours:.0f}h): {article.title[:50]}")
        return False

    ai_score = ai_relevance_score(text)
    if ai_score < config.min_ai_score:
        logger.info(f"  🚫 LOW_AI (score={ai_score}): {article.title[:60]}")
        return False

    if is_promo_content(text):
        logger.info(f"  📢 PROMO: {article.title[:50]}")
        return False

    if is_shopping_content(text):
        logger.info(f"  🛒 SHOPPING: {article.title[:50]}")
        return False

    if is_corporate_news(text):
        logger.info(f"  🏢 CORPORATE: {article.title[:50]}")
        return False

    for bad in BAD_PHRASES:
        if bad in text:
            logger.info(f"  🚫 AD ({bad}): {article.title[:50]}")
            return False

    for ex in HARD_EXCLUDE_KEYWORDS:
        if ex in text:
            logger.info(f"  🚫 HARD_EXCLUDE ({ex}): {article.title[:50]}")
            return False

    if ai_score <= 2:
        for ex in SOFT_EXCLUDE_KEYWORDS:
            if ex in text:
                logger.info(f"  🚫 SOFT_EXCLUDE ({ex}, ai={ai_score}): {article.title[:50]}")
                return False

    if is_economics_news(text):
        logger.info(f"  💵 ECON: {article.title[:50]}")
        return False

    if is_local_us_news(text):
        logger.info(f"  🇺🇸 LOCAL_US: {article.title[:50]}")
        return False

    p_score = priority_score(text)
    subject = detect_subject(text)
    logger.info(f"  ✅ PASS (ai={ai_score}, prio={p_score}, subj={subject}): {article.title[:55]}")
    return True


# ====================== DUPLICATE RESULT ======================
@dataclass
class DuplicateCheckResult:
    is_duplicate: bool
    reasons: List[str]
    max_similarity: float = 0.0
    matched_title: str = ""

    def add_reason(self, reason: str, similarity: float = 0.0, matched: str = ""):
        self.reasons.append(reason)
        if similarity > self.max_similarity:
            self.max_similarity = similarity
            self.matched_title = matched
        self.is_duplicate = True


# ====================== POSTED MANAGER ======================
class PostedManager:
    def __init__(self, db_file: str = "posted_articles.db"):
        self.db_file = db_file
        self._lock = threading.Lock()
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_file, timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA busy_timeout=30000')
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                norm_url TEXT NOT NULL UNIQUE,
                domain TEXT NOT NULL,
                title TEXT NOT NULL,
                title_normalized TEXT NOT NULL,
                title_words TEXT,
                title_word_signature TEXT,
                summary TEXT,
                content_hash TEXT,
                entities TEXT,
                topic TEXT DEFAULT 'general',
                subject TEXT DEFAULT 'other',
                source TEXT,
                posted_date TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rejected_urls (
                norm_url TEXT PRIMARY KEY,
                title TEXT,
                reason TEXT,
                rejected_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        try:
            cursor.execute("ALTER TABLE posted_articles ADD COLUMN subject TEXT DEFAULT 'other'")
            conn.commit()
        except Exception:
            pass

        indices = [
            ('idx_norm_url', 'norm_url'),
            ('idx_content_hash', 'content_hash'),
            ('idx_domain', 'domain'),
            ('idx_posted_date', 'posted_date'),
            ('idx_title_normalized', 'title_normalized'),
            ('idx_title_word_signature', 'title_word_signature'),
            ('idx_subject', 'subject'),
        ]
        for idx_name, column in indices:
            try:
                cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON posted_articles({column})')
            except Exception:
                pass

        conn.commit()
        logger.info("📚 База данных инициализирована")

    def _was_rejected(self, norm_url: str) -> bool:
        cursor = self._get_conn().cursor()
        cursor.execute('SELECT 1 FROM rejected_urls WHERE norm_url = ?', (norm_url,))
        return cursor.fetchone() is not None

    def _add_rejected(self, norm_url: str, title: str, reason: str):
        try:
            cursor = self._get_conn().cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO rejected_urls (norm_url, title, reason) VALUES (?, ?, ?)',
                (norm_url, title[:200], reason)
            )
            self._get_conn().commit()
        except Exception as e:
            logger.error(f"Ошибка добавления в rejected: {e}")

    def is_duplicate(self, url: str, title: str, summary: str = "") -> DuplicateCheckResult:
        result = DuplicateCheckResult(is_duplicate=False, reasons=[])

        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            norm_url = normalize_url(url)
            title_normalized = normalize_title(title)
            title_words = set(get_title_words(title))
            word_signature = get_sorted_word_signature(title)
            content_hash = get_content_hash(f"{title} {summary}")
            entities = extract_entities(f"{title} {summary}")
            domain = get_domain(url)

            if self._was_rejected(norm_url):
                result.add_reason("PREVIOUSLY_REJECTED")
                return result

            cursor.execute('SELECT title FROM posted_articles WHERE norm_url = ?', (norm_url,))
            row = cursor.fetchone()
            if row:
                result.add_reason("URL_EXACT", 1.0, row[0])
                return result

            if content_hash:
                cursor.execute('SELECT title FROM posted_articles WHERE content_hash = ?', (content_hash,))
                row = cursor.fetchone()
                if row:
                    result.add_reason("CONTENT_HASH", 1.0, row[0])
                    return result

            cursor.execute('SELECT title FROM posted_articles WHERE title_normalized = ?', (title_normalized,))
            row = cursor.fetchone()
            if row:
                result.add_reason("TITLE_EXACT", 1.0, row[0])
                return result

            cursor.execute('SELECT title FROM posted_articles WHERE title_word_signature = ?', (word_signature,))
            row = cursor.fetchone()
            if row:
                result.add_reason("WORD_SIGNATURE", 0.95, row[0])
                return result

            cursor.execute(
                'SELECT id, title, title_normalized, title_words, entities, domain FROM posted_articles')
            all_posts = cursor.fetchall()

            for row in all_posts:
                existing_id, existing_title, existing_normalized, existing_words_json, existing_entities_json, existing_domain = row

                seq_sim = calculate_similarity(title_normalized, existing_normalized)
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)

                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)

                if existing_words_json:
                    try:
                        existing_words = set(json.loads(existing_words_json))
                        jaccard = jaccard_similarity(title_words, existing_words)
                        if jaccard > config.jaccard_threshold:
                            result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)
                    except Exception:
                        pass

                if entities and existing_entities_json:
                    try:
                        existing_entities = set(json.loads(existing_entities_json))
                        if len(entities) >= 2 and len(existing_entities) >= 2:
                            common = entities & existing_entities
                            min_size = min(len(entities), len(existing_entities))
                            overlap = len(common) / min_size if min_size > 0 else 0
                            if len(common) >= 2 and overlap >= config.entity_overlap_threshold:
                                result.add_reason(f"ENTITY ({len(common)})", overlap, existing_title)
                    except Exception:
                        pass

                if domain == existing_domain:
                    same_sim = calculate_similarity(title_normalized, existing_normalized)
                    if same_sim > config.same_domain_similarity:
                        result.add_reason(f"SAME_DOMAIN ({same_sim:.0%})", same_sim, existing_title)

            return result

        def check_subject_freshness(self, subject: str) -> Tuple[bool, str]:
        if subject == "other":
            return True, ""

        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute(
                '''SELECT title, posted_date FROM posted_articles 
                   WHERE subject = ? 
                   ORDER BY posted_date DESC LIMIT 1''',
                (subject,)
            )
            row = cursor.fetchone()

            if row:
                try:
                    date_str = row[1]
                    # Парсим дату из SQLite
                    if 'T' in date_str:
                        last_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    else:
                        last_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Делаем aware если naive
                    if last_date.tzinfo is None:
                        last_date = last_date.replace(tzinfo=timezone.utc)
                        
                except Exception:
                    last_date = datetime.now(timezone.utc)

                hours_ago = (datetime.now(timezone.utc) - last_date).total_seconds() / 3600

                if hours_ago < config.same_subject_hours:
                    return False, f"SAME_SUBJECT ({subject}, {hours_ago:.0f}h ago): {row[0][:40]}"

            return True, ""

    def check_diversity(self, topic: str, source: str = "", subject: str = "") -> Tuple[bool, str]:
        with self._lock:
            cursor = self._get_conn().cursor()

            if source:
                cursor.execute(
                    'SELECT source FROM posted_articles ORDER BY posted_date DESC LIMIT 2'
                )
                recent_sources = [row[0] for row in cursor.fetchall()]

                if len(recent_sources) >= 2 and all(s == source for s in recent_sources):
                    return False, f"SAME_SOURCE_3X: {source}"

                if recent_sources and recent_sources[0] == source:
                    return False, f"SAME_SOURCE_CONSECUTIVE: {source}"

            if topic == Topic.GENERAL:
                return True, ""

            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,)
            )
            recent_topics = [row[0] for row in cursor.fetchall()]

            if not recent_topics:
                return True, ""

            if recent_topics[0] == topic:
                return False, f"SAME_AS_LAST: {topic}"

            same_count = sum(1 for t in recent_topics if t == topic)
            if same_count >= config.same_topic_limit:
                return False, f"TOO_MANY: {same_count}/{config.diversity_window} = {topic}"

            return True, ""

    def add(self, article: Article, topic: str = Topic.GENERAL, subject: str = "other") -> bool:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            norm_url = normalize_url(article.link)
            domain_val = get_domain(article.link)
            title_normalized = normalize_title(article.title)
            title_words = list(get_title_words(article.title))
            word_signature = get_sorted_word_signature(article.title)
            content_hash = get_content_hash(f"{article.title} {article.summary}")
            entities = list(extract_entities(f"{article.title} {article.summary}"))

            try:
                cursor.execute('''
                    INSERT INTO posted_articles 
                    (url, norm_url, domain, title, title_normalized, title_words, 
                     title_word_signature, summary, content_hash, entities, topic, subject, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.link, norm_url, domain_val, article.title, title_normalized,
                    json.dumps(title_words), word_signature, article.summary[:1000],
                    content_hash, json.dumps(entities), topic, subject, article.source
                ))

                conn.commit()

                cursor.execute('SELECT id FROM posted_articles WHERE norm_url = ?', (norm_url,))
                saved = cursor.fetchone()

                if saved:
                    logger.info(f"💾 Сохранено (ID={saved[0]}, subj={subject}): {article.title[:50]}...")
                    return True
                else:
                    logger.error(f"❌ Не сохранено: {article.title[:50]}")
                    return False

            except sqlite3.IntegrityError:
                logger.warning(f"⚠️ Уже существует: {article.title[:40]}")
                return False
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                return False

    def log_rejected(self, article: Article, reason: str):
        norm_url = normalize_url(article.link)
        self._add_rejected(norm_url, article.title, reason)
        logger.info(f"🚫 [{reason}]: {article.title[:50]}")

    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, topic, source, posted_date, subject 
                FROM posted_articles 
                ORDER BY posted_date DESC 
                LIMIT ?
            ''', (limit,))

            results = []
            for r in cursor.fetchall():
                results.append({
                    'title': r[0], 'topic': r[1], 'source': r[2],
                    'date': r[3], 'subject': r[4] if len(r) > 4 else 'other'
                })
            return results

    def cleanup(self, days: int = 90):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute(f"DELETE FROM posted_articles WHERE posted_date < datetime('now', '-{days} days')")
            deleted = cursor.rowcount

            cursor.execute(
                f"DELETE FROM rejected_urls WHERE rejected_at < datetime('now', '-{config.rejected_retention_days} days')")
            rejected_deleted = cursor.rowcount

            conn.commit()

            if deleted > 0 or rejected_deleted > 0:
                logger.info(f"🧹 Очищено: {deleted} posted, {rejected_deleted} rejected")

    def get_stats(self) -> dict:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT COUNT(*) FROM posted_articles')
            total = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM rejected_urls')
            rejected = cursor.fetchone()[0]
            return {'total_posted': total, 'total_rejected': rejected}

    def verify_db(self) -> bool:
        with self._lock:
            try:
                cursor = self._get_conn().cursor()
                cursor.execute('PRAGMA integrity_check')
                return cursor.fetchone()[0] == 'ok'
            except Exception:
                return False

    def close(self):
        if self._conn:
            try:
                self._conn.commit()
                self._conn.close()
                logger.info("🔒 БД закрыта")
            except Exception as e:
                logger.error(f"❌ Ошибка закрытия: {e}")
            finally:
                self._conn = None


# ====================== AUTO-CLEANUP ======================
def auto_cleanup_economics(posted: PostedManager):
    logger.info("🧹 Проверка экономических постов...")

    econ_terms = [
        "inflation", "federal reserve", "fed rate", "recession", "fed ",
        "gdp", "unemployment", "stock market", "bonds", "treasury",
        "инфляция", "фрс", "бостик", "уорша", "процентная ставка",
        "центральный банк", "валюта", "экономический рост"
    ]

    with posted._lock:
        conn = posted._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, summary FROM posted_articles")
        all_posts = cursor.fetchall()

        deleted = 0
        for post_id, title, summary in all_posts:
            text = f"{title} {summary}".lower()
            econ_count = sum(1 for term in econ_terms if term in text)
            if econ_count >= 2:
                ai_kw = ["ai", "artificial intelligence", "machine learning",
                         "llm", "gpt", "claude", "gemini", "нейро", "ии",
                         "нейросет", "искусственный интеллект"]
                if not any(kw in text for kw in ai_kw):
                    cursor.execute("DELETE FROM posted_articles WHERE id = ?", (post_id,))
                    deleted += 1

        conn.commit()
        if deleted > 0:
            logger.info(f"🗑️ Удалено {deleted} экономических постов")
        else:
            logger.info("✅ Экономических постов не найдено")


def auto_cleanup_non_ai(posted: PostedManager):
    logger.info("🧹 Проверка не-AI постов...")

    with posted._lock:
        conn = posted._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, summary FROM posted_articles")
        all_posts = cursor.fetchall()

        deleted = 0
        for post_id, title, summary in all_posts:
            text = f"{title} {summary}".lower()
            score = 0
            for kw in AI_KEYWORDS_STRONG:
                if kw in text:
                    score += 2
            for kw in AI_KEYWORDS_WEAK:
                if kw in text:
                    score += 1
            if score < 2:
                cursor.execute("DELETE FROM posted_articles WHERE id = ?", (post_id,))
                deleted += 1

        conn.commit()
        if deleted > 0:
            logger.info(f"🗑️ Удалено {deleted} не-AI постов")
        else:
            logger.info("✅ Все посты про AI")


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


# ====================== FILTERING ======================
def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("🔍 Фильтрация...")
    logger.info(f"   Входящих статей: {len(articles)}")

    candidates = []
    seen_normalized_titles: Set[str] = set()
    seen_word_signatures: Set[str] = set()
    seen_content_hashes: Set[str] = set()

    stats = {
        "batch_dup": 0, "db_dup": 0, "diversity": 0, "passed": 0,
        "filtered_out": 0, "same_subject": 0
    }

    for article in articles:
        if not is_relevant(article):
            stats["filtered_out"] += 1
            continue

        title_normalized = normalize_title(article.title)
        if title_normalized in seen_normalized_titles:
            stats["batch_dup"] += 1
            continue

        word_sig = get_sorted_word_signature(article.title)
        if word_sig in seen_word_signatures:
            stats["batch_dup"] += 1
            continue

        content_hash = get_content_hash(f"{article.title} {article.summary}")
        if content_hash in seen_content_hashes:
            stats["batch_dup"] += 1
            continue

        text = f"{article.title} {article.summary}"
        subject = detect_subject(text)

        if subject != "other":
            subject_count = sum(1 for c in candidates if detect_subject(f"{c.title} {c.summary}") == subject)
            if subject_count >= 2:
                logger.info(f"  🔄 BATCH_SAME_SUBJECT ({subject}): {article.title[:50]}")
                stats["same_subject"] += 1
                continue

        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            reason = "; ".join(dup_result.reasons[:3])
            posted.log_rejected(article, reason)
            stats["db_dup"] += 1
            continue

        subj_ok, subj_reason = posted.check_subject_freshness(subject)
        if not subj_ok:
            posted.log_rejected(article, subj_reason)
            stats["same_subject"] += 1
            continue

        topic = Topic.detect(text)
        div_ok, div_reason = posted.check_diversity(topic, article.source, subject)
        if not div_ok:
            posted.log_rejected(article, div_reason)
            stats["diversity"] += 1
            continue

        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)

        candidates.append(article)
        stats["passed"] += 1

    def score(art: Article) -> float:
        text = f"{art.title} {art.summary}"
        entities = extract_entities(text)
        age = (datetime.now(timezone.utc) - art.published).total_seconds() / 3600
        ai_sc = ai_relevance_score(text)
        prio_sc = priority_score(text)

        source_count = sum(1 for c in candidates if c.source == art.source)
        source_penalty = max(0, source_count - 2) * 5

        subj = detect_subject(text)
        subj_count = sum(1 for c in candidates if detect_subject(f"{c.title} {c.summary}") == subj)
        subj_penalty = max(0, subj_count - 1) * 4

        return (ai_sc * 3 + prio_sc * 5 + len(entities) * 1.5
                + max(0, 72 - age) / 72 - source_penalty - subj_penalty)

    candidates.sort(key=score, reverse=True)

    logger.info(f"📊 Итоги фильтрации:")
    logger.info(f"   filtered={stats['filtered_out']}, batch_dup={stats['batch_dup']}, "
                f"db_dup={stats['db_dup']}, diversity={stats['diversity']}, "
                f"same_subject={stats['same_subject']}, passed={stats['passed']}")
    logger.info(f"✅ Кандидатов: {len(candidates)} из {len(articles)}")

    return candidates


# ====================== TEXT GENERATION ======================
async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"📝 Генерация: {article.title[:55]}...")

    prompt = f"""Ты — редактор Telegram-канала про AI-технологии и новинки для аудитории из РФ и СНГ.

НОВОСТЬ:
Заголовок: {article.title}
Содержание: {article.summary[:800]}
Источник: {article.source}

ЗАДАЧА: Напиши пост для Telegram-канала про AI-НОВИНКИ и ТЕХНОЛОГИИ.

ФОКУС КАНАЛА — только это:
🟢 Новые AI-модели, релизы, обновления (GPT-5, Claude 4, Gemini 2 и т.д.)
🟢 Новые AI-продукты и сервисы (приложения, боты, инструменты)
🟢 Прорывы в AI-исследованиях (бенчмарки, новые архитектуры)
🟢 Практическое применение AI (медицина, образование, кодинг)
🟢 AI-инструменты для обычных людей (генерация картинок, текстов, видео)

НЕ ПОДХОДИТ — ответь SKIP:
🔴 Кадровые перестановки (кто уволен, кто назначен CEO)
🔴 Корпоративные скандалы, суды, иски
🔴 Финансовые отчёты, выручка, акции
🔴 Внутренняя политика компаний (реструктуризация, слияния)
🔴 Скидки на гаджеты, обзоры телефонов
🔴 Политика США без связи с AI

СТРУКТУРА ПОСТА:
1. 🔥 Цепляющий заголовок на русском
2. Что нового — конкретные факты (название, версия, возможности)
3. Чем полезно — как это можно использовать прямо сейчас
4. Вывод (1-2 предложения)

ТРЕБОВАНИЯ:
✅ Длина: 700-1000 символов
✅ Конкретика: цифры, названия, даты
✅ Живой разговорный стиль

ЗАПРЕЩЕНО:
❌ "стоит отметить", "интересно, что", "важно понимать"
❌ Общие фразы без конкретики
❌ Пересказ пресс-релиза без анализа

ПОСТ:"""

    for model in GROQ_MODELS:
        for attempt in range(config.groq_retries_per_model):
            try:
                await asyncio.sleep(1)
                logger.info(f"  🤖 {model} (попытка {attempt + 1})")

                resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model,
                    temperature=0.7,
                    max_tokens=1200,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.choices[0].message.content.strip()

                if "SKIP" in text.upper()[:10]:
                    logger.info("  ⏭️ SKIP (не подходит)")
                    return None

                if len(text) < config.min_post_length:
                    logger.warning(f"  ⚠️ Короткий ({len(text)}), следующая модель...")
                    break

                water_phrases = ["стоит отметить", "важно понимать", "интересно, что",
                                 "давайте разберёмся", "как мы знаем", "не секрет",
                                 "нельзя не отметить", "следует подчеркнуть"]
                if any(w in text.lower() for w in water_phrases):
                    logger.warning("  ⚠️ Вода, повтор...")
                    continue

                topic = Topic.detect(f"{article.title} {article.summary}")
                hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])

                cta = "\n\n🔥 — огонь  |  🗿 — мимо  |  ⚡ — интересно"
                source_link = f'\n\n🔗 <a href="{article.link}">Источник</a>'

                final = f"{text}{cta}\n\n{hashtags}{source_link}"

                logger.info(f"  ✅ [{model}]: {len(text)} симв.")
                return final

            except Exception as e:
                error_str = str(e).lower()
                if any(x in error_str for x in ["decommissioned", "deprecated", "not found"]):
                    logger.warning(f"  ⚠️ {model} недоступна")
                    break
                logger.error(f"  ❌ {model}: {e}")
                await asyncio.sleep(config.groq_base_delay * (2 ** attempt))

    logger.error("  ❌ Все модели не сработали")
    return None


# ====================== POSTING ======================
async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    topic = Topic.detect(f"{article.title} {article.summary}")
    subject = detect_subject(f"{article.title} {article.summary}")

    try:
        logger.info(f"  📤 Отправка поста...")
        await bot.send_message(
            config.channel_id,
            text,
            disable_web_page_preview=False
        )

        saved = posted.add(article, topic, subject)
        if saved:
            logger.info(f"✅ ОПУБЛИКОВАНО [{topic}][{subject}][{article.source}]: {article.title[:50]}")
        return True

    except Exception as e:
        logger.error(f"❌ Telegram: {e}")
        return False


# ====================== MAIN ======================
async def main():
    logger.info("=" * 60)
    logger.info("🚀 AI-POSTER v11.1 (Product Focus + Corporate Filter)")
    logger.info("=" * 60)

    posted = PostedManager(config.db_file)

    try:
        if posted.verify_db():
            logger.info("✅ БД OK")
        else:
            logger.error("❌ Проблема с БД!")
            return

        posted.cleanup(config.retention_days)
        auto_cleanup_economics(posted)
        auto_cleanup_non_ai(posted)

        stats = posted.get_stats()
        logger.info(f"📊 Статистика: {stats['total_posted']} posted, {stats['total_rejected']} rejected")

        recent = posted.get_recent_posts(5)
        if recent:
            logger.info("📋 Последние посты:")
            for p in recent:
                subj = p.get('subject', '?')
                logger.info(f"   • [{p['topic']}][{subj}][{p.get('source', '?')}] {p['title'][:50]}...")

        raw = await load_all_feeds()

        sources_count = {}
        for art in raw:
            sources_count[art.source] = sources_count.get(art.source, 0) + 1
        logger.info(f"📰 Источники: {sources_count}")

        working = sum(1 for v in sources_count.values() if v > 0)
        logger.info(f"📡 Работающих источников: {working}/{len(RSS_FEEDS)}")

        candidates = filter_and_dedupe(raw, posted)

        if not candidates:
            logger.info("📭 Нет подходящих новостей")
            return

        logger.info(f"🎯 Топ кандидаты:")
        for i, c in enumerate(candidates[:7]):
            text = f"{c.title} {c.summary}"
            prio = priority_score(text)
            ai_sc = ai_relevance_score(text)
            subj = detect_subject(text)
            logger.info(f"  {i + 1}. [ai={ai_sc}, subj={subj}] [{c.source}] {c.title[:55]}")

        for article in candidates[:25]:
            dup_result = posted.is_duplicate(article.link, article.title, article.summary)
            if dup_result.is_duplicate:
                posted.log_rejected(article, f"FINAL: {'; '.join(dup_result.reasons[:2])}")
                continue

            summary = await generate_summary(article)
            if not summary:
                posted.log_rejected(article, "GENERATION_FAILED")
                continue

            if await post_article(article, summary, posted):
                logger.info("\n🏁 Готово!")
                break

            await asyncio.sleep(2)
        else:
            logger.info("😔 Не удалось опубликовать")

    finally:
        posted.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())














































































































































































































































































