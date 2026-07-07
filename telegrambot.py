#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
import signal
import sys
from datetime import datetime, timezone
from typing import List, Set, Optional, Tuple, Dict
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

# ====================== Р›РћР“Р ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("block_ai_poster.log", encoding="utf-8"),
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
        self.retention_days = 90
        self.db_file = "posted_articles.db"

        self.title_similarity_threshold = 0.60
        self.ngram_similarity_threshold = 0.55
        self.jaccard_threshold = 0.55
        self.same_domain_similarity = 0.65

        self.subject_window_hours = 48
        self.max_posts_per_subject = 10
        self.subject_min_interval_hours = 1
        self.same_subject_similarity_threshold = 0.70

        self.alternation_enabled = True

        self.min_post_length = 700  # РЈРІРµР»РёС‡РµРЅРѕ СЃ 600
        self.max_article_age_hours = 720
        self.min_ai_score = 1
        self.max_repeat_sentences = 2

        self.diversity_window = 8
        self.same_topic_limit = 4

        self.rotation_history_size = 10
        self.rotation_max_per_source = 6

        self.source_min_posts_between = 1
        self.source_max_in_window = 6

        self.batch_subject_limit = 10

        self.groq_retries_per_model = 2
        self.groq_base_delay = 2.0
        self.telegram_timeout = 30
        self.http_timeout = 60

        missing = []
        for var, name in [(self.groq_api_key, "GROQ_API_KEY"),
                          (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                          (self.channel_id, "CHANNEL_ID")]:
            if not var:
                missing.append(name)
        if missing:
            raise SystemExit(f"вќЊ РћС‚СЃСѓС‚СЃС‚РІСѓСЋС‚: {', '.join(missing)}")


config = Config()

bot: Optional[Bot] = None
groq_client: Optional[Groq] = None

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def init_clients():
    global bot, groq_client
    try:
        bot = Bot(
            token=config.telegram_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logger.info("вњ… Telegram Bot РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅ")
    except Exception as e:
        logger.error(f"вќЊ РћС€РёР±РєР° РёРЅРёС†РёР°Р»РёР·Р°С†РёРё Telegram Bot: {e}")
        raise
    try:
        groq_client = Groq(api_key=config.groq_api_key)
        logger.info("вњ… Groq client РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅ")
    except Exception as e:
        logger.error(f"вќЊ РћС€РёР±РєР° РёРЅРёС†РёР°Р»РёР·Р°С†РёРё Groq: {e}")
        raise


GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# ====================== RSS FEEDS ======================
RSS_FEEDS = [
    ("https://roskomsvoboda.org/feed/", "Р РѕСЃРєРѕРјСЃРІРѕР±РѕРґР°"),
    ("https://rkn.gov.ru/rss/news.xml", "Р РљРќ"),
    ("https://www.comnews.ru/rss/news", "ComNews"),
    ("https://news.google.com/rss/search?q=Р±Р»РѕРєРёСЂРѕРІРєР°+Р РљРќ+VPN+СЂРѕСЃСЃРёСЏ&hl=ru&gl=RU&ceid=RU:ru", "Google News (Р‘Р»РѕРєРёСЂРѕРІРєРё)"),
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
    ("https://news.ycombinator.com/rss", "Hacker News"),
    ("https://habr.com/ru/rss/feed/1cf1798b4d67ac63d1869bba8f26920f"
     "?fl=ru&complexity=high&rating=10&types%5B%5D=article"
     "&types%5B%5D=post&types%5B%5D=news", "Habr AI"),
]

# ---------- РљР›Р®Р§Р•Р’Р«Р• РЎР›РћР’Рђ ----------
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
    "reinforcement learning", "ai safety", "agi",
    "РЅРµР№СЂРѕСЃРµС‚СЊ", "РЅРµР№СЂРѕСЃРµС‚Рё", "РёСЃРєСѓСЃСЃС‚РІРµРЅРЅС‹Р№ РёРЅС‚РµР»Р»РµРєС‚",
    "РјР°С€РёРЅРЅРѕРµ РѕР±СѓС‡РµРЅРёРµ", "РіРµРЅРµСЂР°С‚РёРІРЅС‹Р№ РёРё",
]

AI_KEYWORDS_WEAK = [
    "ai", "nvidia", "copilot", "generative",
    "multimodal", "reasoning", "inference", "embedding",
    "robotics", "humanoid", "automation",
    "nlp", "ai model", "ai training",
    "Р±РѕС‚", "Р±РѕС‚С‹", "Р°РІС‚РѕРјР°С‚РёР·Р°С†РёСЏ",
    "РЅРµР№СЂРѕ", "РёРё",
]

BLOCK_KEYWORDS = [
    "Р±Р»РѕРєРёСЂРѕРІРєР°", "Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ", "СЂРµРµСЃС‚СЂ СЂРєРЅ", "roskomnadzor", "rkn",
    "РѕР±С…РѕРґ Р±Р»РѕРєРёСЂРѕРІРѕРє", "dpi", "Р·Р°РјРµРґР»РµРЅРёРµ С‚СЂР°С„РёРєР°", "sniffing",
    "vless", "v2ray", "xray", "wireguard", "openvpn", "amnezia",
    "Р±РµР»С‹Р№ СЃРїРёСЃРѕРє", "whitelist", "РїСЂРѕРєСЃРё", "С‚СѓРЅРЅРµР»РёСЂРѕРІР°РЅРёРµ",
    "utls", "fragment", "Р°РЅС‚РёР·Р°РїСЂРµС‚", "antizapret",
    "Р·Р°РјРµРґР»РµРЅРёРµ youtube", "Р·Р°РјРµРґР»РµРЅРёРµ СЋС‚СѓР±",
]

GAMES_EXCLUDE = [
    "ps5", "xbox", "nintendo", "game review", "baldur's gate", "roblox", "esports",
    "twitch streamer", "fortnite", "РёРіСЂР°", "РёРіСЂРѕРІР°СЏ", "РіРµР№РјРёРЅРі", "РєРёР±РµСЂСЃРїРѕСЂС‚"
]

BUSINESS_EXCLUDE = [
    "steps down", "resigns", "fired", "laid off", "layoffs", "new ceo", "new cto",
    "promoted to", "departing", "leaves company", "board meeting", "shareholder",
    "quarterly earnings", "earnings call", "revenue report", "stock price", "ipo",
    "merger", "acquisition", "lawsuit filed", "sued by", "legal battle",
    "СѓС…РѕРґРёС‚", "СѓРІРѕР»РµРЅ", "СѓРІРѕР»СЊРЅРµРЅРёРµ", "СЃРѕРєСЂР°С‰РµРЅРёРµ", "РЅР°Р·РЅР°С‡РµРЅ", "РїРѕРєРёРґР°РµС‚",
    "СЃРѕРІРµС‚ РґРёСЂРµРєС‚РѕСЂРѕРІ", "Р°РєС†РёРѕРЅРµСЂС‹", "РєРІР°СЂС‚Р°Р»СЊРЅС‹Р№ РѕС‚С‡С‘С‚", "РІС‹СЂСѓС‡РєР°", "РєР°РїРёС‚Р°Р»РёР·Р°С†РёСЏ",
    "СЃР»РёСЏРЅРёРµ", "РїРѕРіР»РѕС‰РµРЅРёРµ", "СЃСѓРґРµР±РЅС‹Р№ РёСЃРє"
]

PROMO_PATTERNS = [
    "newsletter", "СЂР°СЃСЃС‹Р»РєР°", "РїРѕРґРїРёС€РёС‚РµСЃСЊ", "subscribe", "sign up",
    "free trial", "СЃРєРёРґРєР° РЅР° РїРѕРґРїРёСЃРєСѓ", "РІРµР±РёРЅР°СЂ", "webinar", "buy now", "special offer",
    "РєСѓРїРёС‚СЊ vpn", "vpn СЃРµСЂРІРёСЃ", "С‚Р°СЂРёС„", "РїСЂРѕРјРѕРєРѕРґ"
]

REVIEW_KEYWORDS = ["review", "tested", "hands-on", "РѕР±Р·РѕСЂ", "С‚РµСЃС‚", "СЃРєРёРґРєР°", "discount", "deal", "best", "top 10"]

# Р¤РёР»СЊС‚СЂ РјСѓСЃРѕСЂР° (РІР°РєР°РЅСЃРёРё, СЂР°Р±РѕС‚Р°)
JUNK_KEYWORDS = [
    "РІР°РєР°РЅСЃРёСЏ", "РёС‰РµС‚ РјРµРЅРµРґР¶РµСЂР°", "С‚СЂРµР±СѓРµС‚СЃСЏ", "softline РёС‰РµС‚", "РјРµРЅРµРґР¶РµСЂ РїСЂРѕРґСѓРєС‚Р°",
    "РІР°РєР°РЅСЃРёСЏ", "СЂРµР·СЋРјРµ", "СЂР°Р±РѕС‚Р°", "СЃРѕС‚СЂСѓРґРЅРёРє", "РЅР°РЅРёРјР°РµС‚", "hr", "СЂРµРєСЂСѓС‚РёРЅРі"
]

# ====================== Р“Р•Рћ-Р¤РР›Р¬РўР  (С‚РµРїРµСЂСЊ С‚РѕР»СЊРєРѕ РґР»СЏ Р±Р»РѕРєРёСЂРѕРІРѕРє, РґР»СЏ AI РЅРµ РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ) ======================
RUSSIA_KEYWORDS = ["СЂРѕСЃСЃРёСЏ", "СЂС„", "РјРёРЅС†", "РіРѕСЃРґСѓРјР°", "РїСѓС‚РёРЅ", "РјРѕСЃРєРІР°", "СЃР°РЅРєС‚-РїРµС‚РµСЂР±СѓСЂРі", "СЃРѕРІРµС‚ С„РµРґРµСЂР°С†РёРё", "РєСЂРµРјР»СЊ", "РїСЂР°РІРёС‚РµР»СЊСЃС‚РІРѕ СЂС„", "СЂРѕСЃРєРѕРјРЅР°РґР·РѕСЂ", "СЂРєРЅ"]


@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Topic:
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    ROBOTICS = "robotics"
    HARDWARE = "hardware"
    MESSENGER = "messenger"
    GENERAL = "general"
    BLOCK = "block"
    BYPASS = "bypass"
    WHITELIST = "whitelist"

    HASHTAGS = {
        LLM: "#ChatGPT #LLM #OpenAI #РЅРµР№СЂРѕСЃРµС‚Рё",
        IMAGE_GEN: "#Midjourney #StableDiffusion #РРРђСЂС‚",
        ROBOTICS: "#СЂРѕР±РѕС‚С‹ #СЂРѕР±РѕС‚РѕС‚РµС…РЅРёРєР° #Р°РІС‚РѕРјР°С‚РёР·Р°С†РёСЏ",
        HARDWARE: "#NVIDIA #С‡РёРїС‹ #GPU",
        MESSENGER: "#Telegram #РјРµСЃСЃРµРЅРґР¶РµСЂС‹ #Р±РѕС‚С‹",
        GENERAL: "#РР #С‚РµС…РЅРѕР»РѕРіРёРё #AI",
        BLOCK: "#Р РљРќ #Р±Р»РѕРєРёСЂРѕРІРєРё #С†РµРЅР·СѓСЂР°",
        BYPASS: "#Р±Р»РѕРєРёСЂРѕРІРєРё #С†РµРЅР·СѓСЂР°",
        WHITELIST: "#Р±РµР»С‹Р№СЃРїРёСЃРѕРє #РґРѕСЃС‚СѓРїРЅРѕСЃС‚СЊ",
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["Р±Р»РѕРєРёСЂРѕРІРє", "СЂРєРЅ", "roskomnadzor", "Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ", "СЂРµРµСЃС‚СЂ"]):
            return Topic.BLOCK
        if any(x in t for x in ["vless", "v2ray", "xray", "wireguard", "РѕР±С…РѕРґ", "dpi", "Р°РЅС‚РёР·Р°РїСЂРµС‚"]):
            return Topic.BYPASS
        if any(x in t for x in ["Р±РµР»С‹Р№ СЃРїРёСЃРѕРє", "whitelist", "РґРѕСЃС‚СѓРїРЅРѕСЃС‚СЊ СЃР°Р№С‚Р°"]):
            return Topic.WHITELIST
        if any(x in t for x in ["telegram", "С‚РµР»РµРіСЂР°Рј", "РјРµСЃСЃРµРЅРґР¶РµСЂ", "durov"]):
            return Topic.MESSENGER
        if any(x in t for x in ["gpt", "claude", "gemini", "llm", "chatgpt", "llama"]):
            return Topic.LLM
        if any(x in t for x in ["dall-e", "midjourney", "stable diffusion", "sora"]):
            return Topic.IMAGE_GEN
        if any(x in t for x in ["robot", "humanoid", "boston dynamics"]):
            return Topic.ROBOTICS
        if any(x in t for x in ["nvidia", "chip", "gpu", "hardware"]):
            return Topic.HARDWARE
        return Topic.GENERAL


# ---------- Р’РЎРџРћРњРћР“РђРўР•Р›Р¬РќР«Р• Р¤РЈРќРљР¦РР ----------
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


@lru_cache(maxsize=2000)
def normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(
        r'([a-zA-ZР°-СЏРђ-РЇС‘РЃ]+)\s*[-.]?\s*(\d+(?:\.\d+)?)',
        lambda m: m.group(1) + m.group(2).replace('.', ''),
        t
    )
    return t


@lru_cache(maxsize=2000)
def get_title_words(title: str) -> frozenset:
    words = re.findall(r'\b[a-zA-ZР°-СЏРђ-РЇС‘РЃ0-9]+\b', title.lower())
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'to', 'of',
        'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through',
        'during', 'before', 'above', 'below', 'between', 'under',
        'again', 'further', 'then', 'once', 'here', 'there', 'when', 'where',
        'why', 'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
        'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too',
        'very', 'just', 'and', 'but', 'if', 'or', 'because', 'until', 'while',
        'about', 'against', 'its', 'new', 'says', 'said', 'get', 'got', 'gets',
        'make', 'made', 'makes', 'now', 'also', 'first', 'using', 'used', 'use',
        'out', 'up', 'what', 'which', 'who', 'this', 'that', 'these', 'those',
        'it', 'its', 'you', 'your', 'we', 'our', 'they', 'their', 'he', 'she',
        'him', 'her', 'his', 'hers', 'my', 'mine', 'yours', 'ours', 'theirs',
        'Рё', 'РІ', 'РЅР°', 'СЃ', 'РїРѕ', 'РґР»СЏ', 'РѕС‚', 'РёР·', 'Р·Р°', 'РґРѕ', 'РЅРµ',
        'С‡С‚Рѕ', 'РєР°Рє', 'СЌС‚Рѕ', 'РІСЃРµ', 'РµРіРѕ', 'РѕРЅР°', 'РѕРЅРё', 'РјС‹', 'РІС‹', 'РѕРЅ',
        'РЅРѕ', 'С‚Рѕ', 'С‚Р°Рє', 'СѓР¶Рµ', 'РёР»Рё', 'РµС‰С‘', 'РµС‰Рµ', 'РїСЂРё', 'Р±РµР·',
        'С‚РѕР¶Рµ', 'С‚Р°РєР¶Рµ', 'Р±СѓРґРµС‚', 'Р±С‹Р»Р°', 'Р±С‹Р»Рё', 'Р±С‹С‚СЊ', 'РјРѕР¶РµС‚',
        'СЌС‚РѕС‚', 'СЌС‚Р°', 'СЌС‚Рё', 'С‚РѕС‚', 'С‚РѕРіРѕ', 'СЌС‚РѕРіРѕ', 'СЃРІРѕР№', 'СЃРІРѕРё',
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


def get_content_hash(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())[:300]
    return hashlib.md5(normalized.encode()).hexdigest()


def parse_db_datetime(date_str: str) -> datetime:
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def safe_json_loads(value: str, default=None):
    if not value or value in ('null', 'None', '[]', '{}'):
        return default if default is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


# ====================== РЎРљРћР Р« ======================
def ai_relevance_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for kw in AI_KEYWORDS_STRONG:
        if kw in text_lower:
            score += 2
    for kw in AI_KEYWORDS_WEAK:
        if kw in text_lower:
            score += 1
    if score == 0 and ("ai" in text_lower or "РЅРµР№СЂРѕСЃРµС‚СЊ" in text_lower or "РёРё" in text_lower):
        score = 1
    return score


def block_relevance_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for kw in BLOCK_KEYWORDS:
        if kw in text_lower:
            score += 5
    return score


def is_russian_related(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in RUSSIA_KEYWORDS)


def is_promo_content(text: str) -> bool:
    text_lower = text.lower()
    promo_count = sum(1 for p in PROMO_PATTERNS if p in text_lower)
    return promo_count >= 2


def is_junk_content(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in JUNK_KEYWORDS)


# ====================== is_relevant ======================
def is_relevant(article: Article) -> bool:
    text = f"{article.title} {article.summary}".lower()

    age_hours = (datetime.now(timezone.utc) - article.published).total_seconds() / 3600
    if age_hours > config.max_article_age_hours:
        logger.info(f"  вЏ° TOO_OLD ({age_hours:.0f}h): {article.title[:50]}")
        return False

    if any(g in text for g in GAMES_EXCLUDE):
        logger.info(f"  рџЋ® GAME: {article.title[:50]}")
        return False

    if any(b in text for b in BUSINESS_EXCLUDE):
        logger.info(f"  рџЏў BUSINESS: {article.title[:50]}")
        return False

    if is_promo_content(text):
        logger.info(f"  рџ“ў PROMO: {article.title[:50]}")
        return False

    if is_junk_content(text):
        logger.info(f"  рџ—‘пёЏ JUNK (РІР°РєР°РЅСЃРёСЏ/СЂРµРєР»Р°РјР°): {article.title[:50]}")
        return False

    if any(rw in text for rw in REVIEW_KEYWORDS):
        if not any(kw in text for kw in AI_KEYWORDS_STRONG):
            logger.info(f"  рџ“ќ REVIEW/DEAL (РЅРµС‚ СЃРёР»СЊРЅРѕРіРѕ AI): {article.title[:50]}")
            return False

    has_strong_ai = any(kw in text for kw in AI_KEYWORDS_STRONG)
    has_weak_ai = any(kw in text for kw in AI_KEYWORDS_WEAK)
    is_ai = has_strong_ai or (has_weak_ai and config.min_ai_score <= 1)
    is_block = any(kw in text for kw in BLOCK_KEYWORDS)

    # Р‘Р»РѕРє-РЅРѕРІРѕСЃС‚Рё РІСЃРµРіРґР° РїСЂРѕРїСѓСЃРєР°РµРј
    if is_block:
        logger.info(f"  вњ… BLOCK (РїСЂРёРѕСЂРёС‚РµС‚): {article.title[:55]}")
        return True

    # Р”Р»СЏ AI-РЅРѕРІРѕСЃС‚РµР№: СѓР±РёСЂР°РµРј РіРµРѕРіСЂР°С„РёС‡РµСЃРєРёР№ С„РёР»СЊС‚СЂ вЂ“ РїСѓР±Р»РёРєСѓРµРј Р»СЋР±С‹Рµ AI-РЅРѕРІРѕСЃС‚Рё
    if is_ai:
        logger.info(f"  вњ… AI (Р±РµР· РіРµРѕ-С„РёР»СЊС‚СЂР°): {article.title[:55]}")
        return True

    # Р•СЃР»Рё РЅРµ AI Рё РЅРµ Р±Р»РѕРє вЂ“ РѕС‚СЃРµРєР°РµРј
    logger.info(f"  рџљ« NEITHER AI NOR BLOCK: {article.title[:50]}")
    return False


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
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_file, timeout=30.0, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=30000')
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with self._lock:
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
        logger.info("рџ“љ Р‘Р°Р·Р° РґР°РЅРЅС‹С… РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°РЅР°")

    # ---- РћСЃС‚Р°Р»СЊРЅС‹Рµ РјРµС‚РѕРґС‹ (Р±РµР· РѕС‚С‡С‘С‚РѕРІ) ----
    def _add_rejected(self, norm_url: str, title: str, reason: str):
        pass

    def is_rejected(self, url: str) -> Tuple[bool, str]:
        return False, ""

    def get_subject_posts_in_window(self, subject: str, hours: int) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, posted_date, title_normalized, entities
                FROM posted_articles
                WHERE subject = ?
                  AND posted_date > datetime('now', ?)
                ORDER BY posted_date DESC
            ''', (subject, f'-{hours} hours'))
            results = []
            for r in cursor.fetchall():
                results.append({
                    'title': r[0],
                    'date': r[1],
                    'normalized': r[2],
                    'entities': r[3]
                })
            return results

    def get_subject_stats_cached(self, hours: int = 24) -> Dict[str, List[dict]]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject, title, posted_date, title_normalized
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
                ORDER BY posted_date DESC
            ''', (f'-{hours} hours',))
            result: Dict[str, List[dict]] = defaultdict(list)
            for r in cursor.fetchall():
                result[r[0]].append({
                    'title': r[1],
                    'date': r[2],
                    'normalized': r[3]
                })
            return dict(result)

    def get_last_n_subjects(self, n: int = 5) -> List[str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject FROM posted_articles
                ORDER BY posted_date DESC
                LIMIT ?
            ''', (n,))
            return [row[0] for row in cursor.fetchall()]

    def can_post_subject(self, subject: str) -> Tuple[bool, str]:
        return True, ""

    def check_subject_limit(
        self,
        subject: str,
        new_title: str,
        new_entities: Set[str] = None
    ) -> Tuple[bool, str]:
        if subject == "other":
            return True, ""

        recent_posts = self.get_subject_posts_in_window(subject, config.subject_window_hours)

        if len(recent_posts) >= config.max_posts_per_subject:
            return (
                False,
                f"SUBJECT_LIMIT ({subject}: {len(recent_posts)}/"
                f"{config.max_posts_per_subject} Р·Р° {config.subject_window_hours}h)"
            )

        if recent_posts:
            last_date = parse_db_datetime(recent_posts[0]['date'])
            hours_since = (datetime.now(timezone.utc) - last_date).total_seconds() / 3600
            if hours_since < config.subject_min_interval_hours:
                return (
                    False,
                    f"SUBJECT_COOLDOWN ({subject}: {hours_since:.1f}h "
                    f"< {config.subject_min_interval_hours}h)"
                )

        new_normalized = normalize_title(new_title)
        for post in recent_posts:
            sim = calculate_similarity(new_normalized, post['normalized'])
            if sim > config.same_subject_similarity_threshold:
                return False, f"SUBJECT_SIMILAR ({subject}, sim={sim:.0%})"

        return True, ""

    def is_duplicate(self, url: str, title: str, summary: str = "") -> DuplicateCheckResult:
        result = DuplicateCheckResult(is_duplicate=False, reasons=[])
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            norm_url = normalize_url(url)
            title_normalized = normalize_title(title)
            title_words = set(get_title_words(title))
            content_hash = get_content_hash(f"{title} {summary}")
            domain = get_domain(url)

            cursor.execute(
                'SELECT title FROM posted_articles WHERE norm_url = ? '
                'AND posted_date > datetime("now", ?)',
                (norm_url, f'-{config.retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                result.add_reason("URL_EXACT", 1.0, row[0])
                return result

            if content_hash:
                cursor.execute(
                    'SELECT title FROM posted_articles WHERE content_hash = ? '
                    'AND posted_date > datetime("now", ?)',
                    (content_hash, f'-{config.retention_days} days')
                )
                row = cursor.fetchone()
                if row:
                    result.add_reason("CONTENT_HASH", 1.0, row[0])
                    return result

            cursor.execute(
                'SELECT title FROM posted_articles WHERE title_normalized = ? '
                'AND posted_date > datetime("now", ?)',
                (title_normalized, f'-{config.retention_days} days')
            )
            row = cursor.fetchone()
            if row:
                result.add_reason("TITLE_EXACT", 1.0, row[0])
                return result

            cursor.execute('''
                SELECT id, title, title_normalized, title_words, domain
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
            ''', (f'-{config.retention_days} days',))
            all_posts = cursor.fetchall()

            for row in all_posts:
                existing_id, existing_title, existing_normalized, existing_words_json, existing_domain = row

                seq_sim = calculate_similarity(title_normalized, existing_normalized or "")
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)

                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)

                existing_words = set(safe_json_loads(existing_words_json, []))
                if existing_words:
                    jaccard = jaccard_similarity(title_words, existing_words)
                    if jaccard > config.jaccard_threshold:
                        result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)

                if domain == existing_domain:
                    same_sim = calculate_similarity(title_normalized, existing_normalized or "")
                    if same_sim > config.same_domain_similarity:
                        result.add_reason(f"SAME_DOMAIN ({same_sim:.0%})", same_sim, existing_title)

            return result

    def check_diversity(self, topic: str, source: str = "") -> Tuple[bool, str]:
        with self._lock:
            cursor = self._get_conn().cursor()

            if source:
                cursor.execute(
                    'SELECT source FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                    (config.rotation_history_size,)
                )
                recent_sources = [row[0] for row in cursor.fetchall()]
                last_few = recent_sources[:config.source_min_posts_between]

                if source in last_few:
                    pos = last_few.index(source) + 1
                    return (
                        False,
                        f"SOURCE_TOO_RECENT ({source} Р±С‹Р» {pos}-Рј РёР· РїРѕСЃР»РµРґРЅРёС… "
                        f"{config.source_min_posts_between})"
                    )

                source_count = sum(1 for s in recent_sources if s == source)
                if source_count >= config.source_max_in_window:
                    return (
                        False,
                        f"SOURCE_LIMIT ({source}: {source_count}/{config.source_max_in_window} "
                        f"Р·Р° РїРѕСЃР»РµРґРЅРёРµ {config.rotation_history_size})"
                    )

            if topic == Topic.GENERAL:
                return True, ""

            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,)
            )
            recent_topics = [row[0] for row in cursor.fetchall()]
            if not recent_topics:
                return True, ""

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
            try:
                cursor.execute('''
                    INSERT INTO posted_articles
                    (url, norm_url, domain, title, title_normalized, title_words,
                     title_word_signature, summary, content_hash, entities, topic, subject, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.link, norm_url, domain_val, article.title, title_normalized,
                    json.dumps(title_words), word_signature, article.summary[:1000],
                    content_hash, json.dumps([]), topic, subject, article.source
                ))
                conn.commit()
                cursor.execute('SELECT id FROM posted_articles WHERE norm_url = ?', (norm_url,))
                saved = cursor.fetchone()
                if saved:
                    logger.info(f"рџ’ѕ РЎРѕС…СЂР°РЅРµРЅРѕ (ID={saved[0]}, topic={topic}): {article.title[:50]}...")
                    return True
                else:
                    logger.error(f"вќЊ РќРµ СЃРѕС…СЂР°РЅРµРЅРѕ: {article.title[:50]}")
                    return False
            except sqlite3.IntegrityError:
                logger.warning(f"вљ пёЏ РЈР¶Рµ СЃСѓС‰РµСЃС‚РІСѓРµС‚: {article.title[:40]}")
                return False
            except Exception as e:
                logger.error(f"вќЊ РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ: {e}")
                return False

    def log_rejected(self, article: Article, reason: str):
        logger.info(f"рџљ« [{reason}]: {article.title[:50]}")

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

    def get_last_topic(self) -> Optional[str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT 1')
            row = cursor.fetchone()
            return row[0] if row else None

    def cleanup(self, days: int = 90):
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM posted_articles WHERE posted_date < datetime('now', '-{days} days')"
            )
            deleted_posted = cursor.rowcount
            cursor.execute("DELETE FROM rejected_urls")
            deleted_rejected = cursor.rowcount
            conn.commit()
            logger.info(f"рџ§№ РћС‡РёС‰РµРЅРѕ: {deleted_posted} posted, {deleted_rejected} rejected (РІСЃСЏ С‚Р°Р±Р»РёС†Р°)")

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
        with self._lock:
            conn = getattr(self._local, 'conn', None)
            if conn:
                try:
                    conn.commit()
                    conn.close()
                    logger.info("рџ”’ Р‘Р” Р·Р°РєСЂС‹С‚Р°")
                except Exception as e:
                    logger.error(f"вќЊ РћС€РёР±РєР° Р·Р°РєСЂС‹С‚РёСЏ Р‘Р”: {e}")
                finally:
                    self._local.conn = None


# ====================== RSS LOADING ======================
async def fetch_feed(url: str, source: str) -> List[Article]:
    try:
        await asyncio.sleep(random.uniform(0.3, 1.5))
        timeout = aiohttp.ClientTimeout(total=config.http_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=HEADERS) as resp:
                if resp.status != 200:
                    logger.warning(f"  вљ пёЏ {source}: HTTP {resp.status}")
                    return []
                content = await resp.text()
        feed = await asyncio.to_thread(feedparser.parse, content)
        articles = []
        for entry in feed.entries[:20]:
            link = entry.get('link', '').strip()
            title = entry.get('title', '').strip()
            summary = re.sub(r'<[^>]+>', '', entry.get('summary', entry.get('description', '')).strip())
            if not link or not title or len(title) < 15:
                continue
            pub_date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = datetime(*pub_date[:6], tzinfo=timezone.utc) if pub_date else datetime.now(timezone.utc)
            articles.append(Article(title=title, summary=summary, link=link, source=source, published=published))
        logger.info(f"  вњ… {source}: {len(articles)}")
        return articles
    except asyncio.TimeoutError:
        logger.warning(f"  вљ пёЏ {source}: Timeout")
        return []
    except Exception as e:
        logger.warning(f"  вљ пёЏ {source}: {e}")
        return []


async def load_all_feeds() -> List[Article]:
    logger.info("рџ“Ґ Р—Р°РіСЂСѓР·РєР° RSS...")
    tasks = [fetch_feed(url, source) for url, source in RSS_FEEDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"  вљ пёЏ {RSS_FEEDS[i][1]}: {result}")
        elif result:
            all_articles.extend(result)
    logger.info(f"рџ“¦ Р’СЃРµРіРѕ: {len(all_articles)}")
    return all_articles


def interleave_by_source(candidates: List[Article]) -> List[Article]:
    if not candidates:
        return []
    by_source: Dict[str, deque] = {}
    source_order: List[str] = []
    for art in candidates:
        if art.source not in by_source:
            by_source[art.source] = deque()
            source_order.append(art.source)
        by_source[art.source].append(art)
    result = []
    while any(by_source[s] for s in source_order):
        for source in source_order:
            if by_source[source]:
                result.append(by_source[source].popleft())
    return result


# ====================== filter_and_dedupe ======================
def filter_and_dedupe(articles: List[Article], posted: PostedManager) -> List[Article]:
    logger.info("рџ”Ќ Р¤РёР»СЊС‚СЂР°С†РёСЏ...")
    logger.info(f"   Р’С…РѕРґСЏС‰РёС… СЃС‚Р°С‚РµР№: {len(articles)}")

    candidates = []
    seen_normalized_titles: Set[str] = set()
    seen_word_signatures: Set[str] = set()
    seen_content_hashes: Set[str] = set()
    batch_subject_counts: Dict[str, int] = defaultdict(int)

    stats = {
        "batch_dup": 0, "db_dup": 0, "diversity": 0, "passed": 0,
        "filtered_out": 0, "subject_limit": 0, "subject_rotation": 0,
        "batch_subject": 0, "blacklisted": 0,
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
        subject = Topic.detect(text)

        if subject != "other" and batch_subject_counts[subject] >= config.batch_subject_limit:
            logger.info(f"  вЏ­пёЏ BATCH_SUBJECT_LIMIT ({subject}, {batch_subject_counts[subject]} in batch): {article.title[:50]}")
            stats["batch_subject"] += 1
            continue

        subj_ok, subj_reason = posted.check_subject_limit(subject, article.title)
        if not subj_ok:
            logger.info(f"  вЏ­пёЏ {subj_reason}: {article.title[:50]}")
            stats["subject_limit"] += 1
            continue

        dup_result = posted.is_duplicate(article.link, article.title, article.summary)
        if dup_result.is_duplicate:
            reason = "; ".join(dup_result.reasons[:3])
            posted.log_rejected(article, reason)
            stats["db_dup"] += 1
            continue

        topic = subject
        div_ok, div_reason = posted.check_diversity(topic, article.source)
        if not div_ok:
            logger.info(f"  вЏ­пёЏ DIVERSITY ({div_reason}): {article.title[:50]}")
            stats["diversity"] += 1
            continue

        seen_normalized_titles.add(title_normalized)
        seen_word_signatures.add(word_sig)
        if content_hash:
            seen_content_hashes.add(content_hash)

        batch_subject_counts[subject] += 1
        candidates.append(article)
        stats["passed"] += 1

    block_candidates = []
    ai_candidates = []
    for art in candidates:
        text = f"{art.title} {art.summary}".lower()
        if any(kw in text for kw in BLOCK_KEYWORDS):
            block_candidates.append(art)
        else:
            ai_candidates.append(art)

    if block_candidates:
        logger.info(f"рџ”’ РќР°Р№РґРµРЅРѕ {len(block_candidates)} Р±Р»РѕРє-СЃС‚Р°С‚РµР№, Р±РµСЂС‘Рј РёС… РІ РїСЂРёРѕСЂРёС‚РµС‚")
        block_candidates.sort(key=lambda a: block_relevance_score(f"{a.title} {a.summary}"), reverse=True)
        candidates = block_candidates
    else:
        logger.info(f"рџЊђ Р‘Р»РѕРє-РЅРѕРІРѕСЃС‚РµР№ РЅРµС‚, Р±РµСЂС‘Рј AI-СЃС‚Р°С‚СЊРё (Р±РµР· РіРµРѕ-С„РёР»СЊС‚СЂР°)")
        ai_candidates.sort(key=lambda a: ai_relevance_score(f"{a.title} {a.summary}"), reverse=True)
        candidates = ai_candidates[:5]

    candidates = interleave_by_source(candidates)

    logger.info("рџ“Љ РС‚РѕРіРё С„РёР»СЊС‚СЂР°С†РёРё:")
    logger.info(f"   filtered={stats['filtered_out']}, batch_dup={stats['batch_dup']}, db_dup={stats['db_dup']}, diversity={stats['diversity']}")
    logger.info(f"   subject_limit={stats['subject_limit']}, subject_rotation={stats['subject_rotation']}, batch_subject={stats['batch_subject']}, blacklisted={stats['blacklisted']}")
    logger.info(f"вњ… РљР°РЅРґРёРґР°С‚РѕРІ РїРѕСЃР»Рµ РїСЂРёРѕСЂРёС‚РµС‚Р°: {len(candidates)} РёР· {len(articles)}")

    return candidates


def rotate_candidates(candidates: List[Article], posted: PostedManager) -> List[Article]:
    recent = posted.get_recent_posts(config.rotation_history_size)
    if not recent:
        return candidates

    recent_sources = [p.get('source', '') for p in recent]
    source_counts: Dict[str, int] = {}
    for src in recent_sources:
        source_counts[src] = source_counts.get(src, 0) + 1
    last_n_sources = recent_sources[:config.source_min_posts_between]

    priority: List[Article] = []
    deprioritized: List[Article] = []

    for art in candidates:
        src = art.source
        if src in last_n_sources:
            pos = last_n_sources.index(src) + 1
            logger.info(f"   в¬‡пёЏ DEPRIO [src {src}] Р±С‹Р» {pos}-Рј РёР· РїРѕСЃР»РµРґРЅРёС… {config.source_min_posts_between}: {art.title[:40]}")
            deprioritized.append(art)
        elif source_counts.get(src, 0) >= config.source_max_in_window:
            logger.info(f"   в¬‡пёЏ DEPRIO [src {src}] x{source_counts[src]} РІ РёСЃС‚РѕСЂРёРё: {art.title[:40]}")
            deprioritized.append(art)
        else:
            priority.append(art)

    result = priority + deprioritized
    return result if result else candidates[:1]


DISCLAIMER = (
    "\n\nвљ пёЏ РћС‚РґРµР»СЊРЅС‹Рµ РѕСЂРіР°РЅРёР·Р°С†РёРё, СѓРїРѕРјСЏРЅСѓС‚С‹Рµ РІ РґР°РЅРЅРѕРј РјР°С‚РµСЂРёР°Р»Рµ, РјРѕРіСѓС‚ РёРјРµС‚СЊ СЃС‚Р°С‚СѓСЃ "
    "В«РЅРµР¶РµР»Р°С‚РµР»СЊРЅС‹С…В» РЅР° С‚РµСЂСЂРёС‚РѕСЂРёРё Р Р¤. РђРєС‚СѓР°Р»СЊРЅС‹Р№ РїРµСЂРµС‡РµРЅСЊ СЂР°Р·РјРµС‰С‘РЅ РЅР° РѕС„РёС†РёР°Р»СЊРЅРѕРј СЃР°Р№С‚Рµ "
    'РњРёРЅСЋСЃС‚Р° Р Р¤: <a href="https://minjust.gov.ru/ru/pages/perechen-inostrannyh-i-'
    'mezhdunarodnyh-organizacij-deyatelnost-kotoryh-priznana-nezhelatelnoj-na-territorii-'
    'rossiyskoy-federacii/">minjust.gov.ru</a>'
)


def has_repeated_sentences(text: str, max_repeats: int = 2) -> bool:
    sentences = re.split(r'[.!?]\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if len(sentences) < 2:
        return False
    repeat_count = 0
    checked = []
    for sent in sentences:
        for prev in checked:
            sim = calculate_similarity(sent, prev)
            if sim > 0.6:
                repeat_count += 1
                if repeat_count >= max_repeats:
                    return True
        checked.append(sent)
    return False


# ====================== Р“Р•РќР•Р РђР¦РРЇ РџРћРЎРўРђ (СѓР»СѓС‡С€РµРЅРЅР°СЏ) ======================
async def generate_summary(article: Article) -> Optional[str]:
    logger.info(f"рџ“ќ Р“РµРЅРµСЂР°С†РёСЏ: {article.title[:55]}...")
    text_for_topic = f"{article.title} {article.summary}"
    topic = Topic.detect(text_for_topic)
    is_block_topic = any(kw in text_for_topic.lower() for kw in BLOCK_KEYWORDS)

    if is_block_topic:
        prompt = f"""РўС‹ вЂ” СЂРµРґР°РєС‚РѕСЂ Telegram-РєР°РЅР°Р»Р° РїСЂРѕ Р±Р»РѕРєРёСЂРѕРІРєРё Рё С†РёС„СЂРѕРІС‹Рµ РѕРіСЂР°РЅРёС‡РµРЅРёСЏ РІ Р Р¤. РќР°РїРёС€Рё РєСЂР°С‚РєРёР№, РЅРѕ Р·Р°РєРѕРЅС‡РµРЅРЅС‹Р№ РїРѕСЃС‚ РїРѕ РЅРѕРІРѕСЃС‚Рё.

РќРћР’РћРЎРўР¬:
Р—Р°РіРѕР»РѕРІРѕРє: {article.title}
РЎРѕРґРµСЂР¶Р°РЅРёРµ: {article.summary[:2000]}
РСЃС‚РѕС‡РЅРёРє: {article.source}

**РЎС‚СЂСѓРєС‚СѓСЂР° РїРѕСЃС‚Р°:**
1. Р’СЃС‚СѓРїР»РµРЅРёРµ: РєСЂР°С‚РєРѕ РІРІРµРґРё РІ С‚РµРјСѓ (1 РїСЂРµРґР»РѕР¶РµРЅРёРµ, РЅР°С‡РЅРё СЃ В«РќРѕРІРѕСЃС‚СЊ:В» РёР»Рё В«Р’ Р РѕСЃСЃРёРё РїСЂРѕРёР·РѕС€Р»РѕвЂ¦В»).
2. РЎСѓС‚СЊ: С‡С‚Рѕ РёРјРµРЅРЅРѕ РїСЂРѕРёР·РѕС€Р»Рѕ? (С„Р°РєС‚С‹: РєС‚Рѕ, С‡С‚Рѕ, РєРѕРіРґР°, РєР°Рє).
3. РџРѕСЃР»РµРґСЃС‚РІРёСЏ: РєР°РєРёРµ РїРѕСЃР»РµРґСЃС‚РІРёСЏ РґР»СЏ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РёР»Рё РёРЅРґСѓСЃС‚СЂРёРё?

**РўСЂРµР±РѕРІР°РЅРёСЏ:**
- РќРµР№С‚СЂР°Р»СЊРЅС‹Р№, РёРЅС„РѕСЂРјР°С‚РёРІРЅС‹Р№ С‚РѕРЅ, Р±РµР· РѕС†РµРЅРѕРє.
- РќРµ СѓРїРѕРјРёРЅР°Р№ РІР»Р°СЃС‚СЊ, РїСЂР°РІРёС‚РµР»СЊСЃС‚РІРѕ, РџСѓС‚РёРЅР° вЂ“ С‚РѕР»СЊРєРѕ С‚РµС…РЅРёС‡РµСЃРєРёРµ РґРµС‚Р°Р»Рё.
- РќРµ Р·Р°РґР°РІР°Р№ РІРѕРїСЂРѕСЃРѕРІ С‡РёС‚Р°С‚РµР»СЏРј.
- Р”Р»РёРЅР°: СЃС‚СЂРѕРіРѕ 600вЂ“800 СЃРёРјРІРѕР»РѕРІ.
- РџРѕСЃС‚ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СЃРІСЏР·РЅС‹Рј Рё С‡РёС‚Р°С‚СЊСЃСЏ РєР°Рє РµРґРёРЅРѕРµ С†РµР»РѕРµ.

РџРћРЎРў:"""
    else:
        prompt = f"""РўС‹ вЂ” СЂРµРґР°РєС‚РѕСЂ Telegram-РєР°РЅР°Р»Р° РїСЂРѕ AI Рё С‚РµС…РЅРѕР»РѕРіРёРё. РќР°РїРёС€Рё РєСЂР°С‚РєРёР№, РЅРѕ Р·Р°РєРѕРЅС‡РµРЅРЅС‹Р№ РїРѕСЃС‚ РїРѕ РЅРѕРІРѕСЃС‚Рё. РўРІРѕР№ РїРѕСЃС‚ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РїРѕРЅСЏС‚РµРЅ РґР°Р¶Рµ С‚РµРј, РєС‚Рѕ РЅРµ С‡РёС‚Р°Р» РёСЃС…РѕРґРЅСѓСЋ СЃС‚Р°С‚СЊСЋ.

РќРћР’РћРЎРўР¬:
Р—Р°РіРѕР»РѕРІРѕРє: {article.title}
РЎРѕРґРµСЂР¶Р°РЅРёРµ: {article.summary[:2000]}
РСЃС‚РѕС‡РЅРёРє: {article.source}

**РЎС‚СЂСѓРєС‚СѓСЂР° РїРѕСЃС‚Р° (СЃС‚СЂРѕРіРѕ СЃРѕР±Р»СЋРґР°Р№):**
1. Р’СЃС‚СѓРїР»РµРЅРёРµ: РєСЂР°С‚РєРѕ РІРІРµРґРё РІ С‚РµРјСѓ (1 РїСЂРµРґР»РѕР¶РµРЅРёРµ). РќР°С‡РЅРё СЃ С„СЂР°Р·С‹ В«РќРѕРІРѕСЃС‚СЊ РґРЅСЏ:В» РёР»Рё В«РљРѕРјРїР°РЅРёСЏ X РѕР±СЉСЏРІРёР»Р° РѕвЂ¦В».
2. РЎСѓС‚СЊ: С‡С‚Рѕ РёРјРµРЅРЅРѕ РїСЂРѕРёР·РѕС€Р»Рѕ? (С„Р°РєС‚С‹: РєС‚Рѕ, С‡С‚Рѕ, РіРґРµ, РєРѕРіРґР°). РџРµСЂРµСЃРєР°Р¶Рё СЃРІРѕРёРјРё СЃР»РѕРІР°РјРё, РЅРѕ СЃРѕС…СЂР°РЅРё РІСЃРµ РєР»СЋС‡РµРІС‹Рµ С„Р°РєС‚С‹.
3. Р—РЅР°С‡РµРЅРёРµ: РїРѕС‡РµРјСѓ СЌС‚Рѕ РІР°Р¶РЅРѕ РґР»СЏ РёРЅРґСѓСЃС‚СЂРёРё, РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РёР»Рё РѕР±С‰РµСЃС‚РІР°? (1вЂ“2 РїСЂРµРґР»РѕР¶РµРЅРёСЏ).

**РўСЂРµР±РѕРІР°РЅРёСЏ:**
- РџРёС€Рё РЅРµР№С‚СЂР°Р»СЊРЅРѕ, Р±РµР· РѕС†РµРЅРѕРє Рё Р»РёС€РЅРёС… СЌРјРѕС†РёР№.
- РќРµ Р·Р°РґР°РІР°Р№ РІРѕРїСЂРѕСЃРѕРІ С‡РёС‚Р°С‚РµР»СЏРј.
- РќРµ РёСЃРїРѕР»СЊР·СѓР№ СЃР»РѕРІР° В«РІР»Р°СЃС‚СЊВ», В«РїСЂР°РІРёС‚РµР»СЊСЃС‚РІРѕВ», В«РїСѓС‚РёРЅВ» Рё С‚.Рї.
- РР·Р±РµРіР°Р№ С€С‚Р°РјРїРѕРІ: В«СЃС‚РѕРёС‚ РѕС‚РјРµС‚РёС‚СЊВ», В«РІР°Р¶РЅРѕ РїРѕРЅРёРјР°С‚СЊВ», В«РґР°РІР°Р№С‚Рµ СЂР°Р·Р±РµСЂС‘РјСЃСЏВ».
- Р”Р»РёРЅР°: СЃС‚СЂРѕРіРѕ 700вЂ“1000 СЃРёРјРІРѕР»РѕРІ (РЅРµ РјРµРЅСЊС€Рµ 700, РЅРµ Р±РѕР»СЊС€Рµ 1000).
- РџРѕСЃС‚ РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ СЃРІСЏР·РЅС‹Рј, С‡РёС‚Р°С‚СЊСЃСЏ РєР°Рє РµРґРёРЅРѕРµ С†РµР»РѕРµ Рё РЅРµ РѕР±СЂС‹РІР°С‚СЊСЃСЏ РЅР° РїРѕР»СѓСЃР»РѕРІРµ.

РџРћРЎРў:"""

    water_phrases = [
        "СЃС‚РѕРёС‚ РѕС‚РјРµС‚РёС‚СЊ", "РІР°Р¶РЅРѕ РїРѕРЅРёРјР°С‚СЊ", "РёРЅС‚РµСЂРµСЃРЅРѕ, С‡С‚Рѕ",
        "РґР°РІР°Р№С‚Рµ СЂР°Р·Р±РµСЂС‘РјСЃСЏ", "РєР°Рє РјС‹ Р·РЅР°РµРј", "РЅРµ СЃРµРєСЂРµС‚",
        "РЅРµР»СЊР·СЏ РЅРµ РѕС‚РјРµС‚РёС‚СЊ", "СЃР»РµРґСѓРµС‚ РїРѕРґС‡РµСЂРєРЅСѓС‚СЊ",
        "РїРѕС‡РµРјСѓ СЌС‚Рѕ РІР°Р¶РЅРѕ", "РґР»СЏ С‡РµРіРѕ СЌС‚Рѕ РІР°Р¶РЅРѕ",
        "СЌС‚Рѕ РІР°Р¶РЅРѕ РїРѕС‚РѕРјСѓ С‡С‚Рѕ", "СЌС‚Рѕ РјРµРЅСЏРµС‚ РІСЃС‘",
        "СЌС‚Рѕ РѕС‚РєСЂС‹РІР°РµС‚ РІРѕР·РјРѕР¶РЅРѕСЃС‚Рё", "СЌС‚Рѕ РјРµРЅСЏРµС‚ РїСЂР°РІРёР»Р°",
        "РјРѕР¶РµС‚ РїСЂРёРІРµСЃС‚Рё", "РјРѕР¶РЅРѕ РѕР¶РёРґР°С‚СЊ", "РІРµСЂРѕСЏС‚РЅРѕ", "РІРѕР·РјРѕР¶РЅРѕ",
        "РѕС‚СЂР°Р¶Р°РµС‚ СЌРєСЃРїРµСЂС‚РёР·Сѓ", "СѓРєСЂРµРїРёС‚ РїРѕР·РёС†РёРё", "РїРѕР»СЊР·РѕРІР°С‚РµР»Рё РјРѕРіСѓС‚ СЂР°СЃСЃС‡РёС‚С‹РІР°С‚СЊ",
    ]

    for model in GROQ_MODELS:
        for attempt in range(config.groq_retries_per_model):
            try:
                await asyncio.sleep(1)
                logger.info(f"  рџ¤– {model} (РїРѕРїС‹С‚РєР° {attempt + 1})")

                # Р”Р»СЏ РІС‚РѕСЂРѕР№ РїРѕРїС‹С‚РєРё РёСЃРїРѕР»СЊР·СѓРµРј С‡СѓС‚СЊ Р±РѕР»РµРµ РІС‹СЃРѕРєСѓСЋ С‚РµРјРїРµСЂР°С‚СѓСЂСѓ
                temp = 0.8 if attempt == 1 else 0.7

                resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model,
                    temperature=temp,
                    max_tokens=1500,  # СѓРІРµР»РёС‡РµРЅРѕ СЃ 1200
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.choices[0].message.content.strip()

                if not is_block_topic and "SKIP" in text.upper()[:10]:
                    logger.info("  вЏ­пёЏ SKIP (РЅРµ РїРѕРґС…РѕРґРёС‚)")
                    return None

                # РџСЂРѕРІРµСЂРєР° РјРёРЅРёРјР°Р»СЊРЅРѕР№ РґР»РёРЅС‹
                if len(text) < config.min_post_length:
                    logger.warning(f"  вљ пёЏ РљРѕСЂРѕС‚РєРёР№ ({len(text)} СЃРёРјРІ., РјРёРЅРёРјСѓРј {config.min_post_length}), СЃР»РµРґСѓСЋС‰Р°СЏ РјРѕРґРµР»СЊ...")
                    break

                # РџСЂРѕРІРµСЂРєР° РЅР° С†РµР»РѕСЃС‚РЅРѕСЃС‚СЊ: РЅРµ РјРµРЅРµРµ 3 РїСЂРµРґР»РѕР¶РµРЅРёР№
                sentences = re.split(r'[.!?]\s+', text)
                if len(sentences) < 3:
                    logger.warning("  вљ пёЏ РњРµРЅСЊС€Рµ 3 РїСЂРµРґР»РѕР¶РµРЅРёР№, РїРµСЂРµРіРµРЅРµСЂР°С†РёСЏ...")
                    continue

                # РџСЂРѕРІРµСЂРєР°: РЅР°С‡РёРЅР°РµС‚СЃСЏ СЃ Р·Р°РіР»Р°РІРЅРѕР№ Р±СѓРєРІС‹
                if not text[0].isupper():
                    logger.warning("  вљ пёЏ РўРµРєСЃС‚ РЅРµ РЅР°С‡РёРЅР°РµС‚СЃСЏ СЃ Р·Р°РіР»Р°РІРЅРѕР№ Р±СѓРєРІС‹, РїРµСЂРµРіРµРЅРµСЂР°С†РёСЏ...")
                    continue

                # РџСЂРѕРІРµСЂРєР° РЅР° РѕР±СЂС‹РІ РІ РєРѕРЅС†Рµ (РїСЂРµРґР»РѕРі, СЃРѕСЋР·, РІРІРѕРґРЅРѕРµ СЃР»РѕРІРѕ)
                if re.search(r'\b(Рё|РІ|РЅР°|СЃ|РїРѕ|РґР»СЏ|РѕС‚|РёР·|Р·Р°|РґРѕ|РЅРµ|С‡С‚Рѕ|РєР°Рє|СЌС‚Рѕ|РІСЃРµ|РµРіРѕ|РѕРЅР°|РѕРЅРё|РјС‹|РІС‹|РѕРЅ|РЅРѕ|С‚Рѕ|С‚Р°Рє|СѓР¶Рµ|РёР»Рё|РµС‰С‘|РµС‰Рµ|РїСЂРё|Р±РµР·|С‚РѕР¶Рµ|С‚Р°РєР¶Рµ|Р±СѓРґРµС‚|Р±С‹Р»Р°|Р±С‹Р»Рё|Р±С‹С‚СЊ|РјРѕР¶РµС‚|СЌС‚РѕС‚|СЌС‚Р°|СЌС‚Рё|С‚РѕС‚|С‚РѕРіРѕ|СЌС‚РѕРіРѕ|СЃРІРѕР№|СЃРІРѕРё)\s*$', text, re.IGNORECASE):
                    logger.warning("  вљ пёЏ РўРµРєСЃС‚ РѕР±СЂС‹РІР°РµС‚СЃСЏ РЅР° РїСЂРµРґР»РѕРіРµ/СЃРѕСЋР·Рµ, РїРµСЂРµРіРµРЅРµСЂР°С†РёСЏ...")
                    continue

                # РџСЂРѕРІРµСЂРєР° РЅР° С€С‚Р°РјРїС‹
                water_count = sum(1 for phrase in water_phrases if phrase in text.lower())
                if water_count >= 3:
                    logger.warning(f"  вљ пёЏ РЁС‚Р°РјРїС‹ ({water_count}), РїРµСЂРµРіРµРЅРµСЂР°С†РёСЏ...")
                    if attempt == config.groq_retries_per_model - 1:
                        logger.warning("  вЏ­пёЏ РџСЂРѕРїСѓСЃРєР°РµРј РёР·-Р·Р° С€С‚Р°РјРїРѕРІ")
                        return None
                    continue

                if has_repeated_sentences(text, config.max_repeat_sentences):
                    logger.warning("  вљ пёЏ РџРѕРІС‚РѕСЂСЏСЋС‰РёРµСЃСЏ РїСЂРµРґР»РѕР¶РµРЅРёСЏ, СЃР»РµРґСѓСЋС‰Р°СЏ РјРѕРґРµР»СЊ...")
                    continue

                # РџРѕСЃС‚-РѕР±СЂР°Р±РѕС‚РєР°: СѓРґР°Р»СЏРµРј СЃР»СѓР¶РµР±РЅС‹Рµ РјР°СЂРєРµСЂС‹
                lines = text.split('\n')
                cleaned_lines = []
                for line in lines:
                    line_stripped = line.strip()
                    if re.match(r'^(РќРћР’РћРЎРўР¬|Р—Р°РіРѕР»РѕРІРѕРє|РЎРѕРґРµСЂР¶Р°РЅРёРµ|РСЃС‚РѕС‡РЅРёРє|РџРћРЎРў|РќРћР’РћРЎРўР¬\s*:|Р—Р°РіРѕР»РѕРІРѕРє\s*:|РЎРѕРґРµСЂР¶Р°РЅРёРµ\s*:|РСЃС‚РѕС‡РЅРёРє\s*:|РџРћРЎРў\s*:)\s*', line_stripped, re.IGNORECASE):
                        continue
                    cleaned_lines.append(line)
                text = '\n'.join(cleaned_lines)
                text = re.sub(r'\bР—Р°РіРѕР»РѕРІРѕРє\s*:\s*', '', text, flags=re.IGNORECASE)
                text = re.sub(r'\bРЎРѕРґРµСЂР¶Р°РЅРёРµ\s*:\s*', '', text, flags=re.IGNORECASE)
                text = re.sub(r'\bРСЃС‚РѕС‡РЅРёРє\s*:\s*', '', text, flags=re.IGNORECASE)
                text = re.sub(r'\bРќРћР’РћРЎРўР¬\s*:\s*', '', text, flags=re.IGNORECASE)

                # РЈР±РµРґРёРјСЃСЏ, С‡С‚Рѕ С‚РµРєСЃС‚ Р·Р°РєР°РЅС‡РёРІР°РµС‚СЃСЏ С‚РѕС‡РєРѕР№
                if not text.endswith(('.', '!', '?')):
                    text += '.'

                hashtags = Topic.HASHTAGS.get(topic, Topic.HASHTAGS[Topic.GENERAL])
                source_link = f'\n\nрџ”— <a href="{article.link}">РСЃС‚РѕС‡РЅРёРє</a>'
                final = f"{text}\n\n{hashtags}{source_link}{DISCLAIMER}"
                logger.info(f"  вњ… [{model}]: {len(text)} СЃРёРјРІ.")
                return final

            except Exception as e:
                error_str = str(e).lower()
                if any(x in error_str for x in ["decommissioned", "deprecated", "not found"]):
                    logger.warning(f"  вљ пёЏ {model} РЅРµРґРѕСЃС‚СѓРїРЅР°, РїСЂРѕРїСѓСЃРєР°РµРј")
                    break
                logger.error(f"  вќЊ {model} РїРѕРїС‹С‚РєР° {attempt + 1}: {e}")
                await asyncio.sleep(config.groq_base_delay * (2 ** attempt))

    logger.error("  вќЊ Р’СЃРµ РјРѕРґРµР»Рё РЅРµ СЃСЂР°Р±РѕС‚Р°Р»Рё")
    return None


async def post_article(article: Article, text: str, posted: PostedManager) -> bool:
    topic = Topic.detect(f"{article.title} {article.summary}")
    subject = topic

    try:
        logger.info("  рџ“¤ РћС‚РїСЂР°РІРєР° РїРѕСЃС‚Р°...")
        await bot.send_message(config.channel_id, text, disable_web_page_preview=False)
        logger.info(f"вњ… РћРџРЈР‘Р›РРљРћР’РђРќРћ [{topic}][{article.source}]: {article.title[:50]}")
    except Exception as e:
        logger.error(f"вќЊ Telegram РѕС€РёР±РєР° РѕС‚РїСЂР°РІРєРё: {e}")
        return False

    saved = posted.add(article, topic, subject)
    if not saved:
        logger.warning(f"вљ пёЏ РџРѕСЃС‚ РѕС‚РїСЂР°РІР»РµРЅ, РЅРѕ РЅРµ СЃРѕС…СЂР°РЅС‘РЅ РІ Р‘Р” (РІРѕР·РјРѕР¶РЅРѕ РґСѓР±Р»СЊ): {article.title[:50]}")
    return True


async def check_telegram_connection() -> bool:
    try:
        logger.info("рџ”Њ РџСЂРѕРІРµСЂРєР° РїРѕРґРєР»СЋС‡РµРЅРёСЏ Рє Telegram...")
        me = await asyncio.wait_for(bot.get_me(), timeout=config.telegram_timeout)
        logger.info(f"вњ… Telegram OK: @{me.username}")
        return True
    except asyncio.TimeoutError:
        logger.error("вќЊ Telegram: Timeout РїСЂРё РїРѕРґРєР»СЋС‡РµРЅРёРё")
        return False
    except Exception as e:
        logger.error(f"вќЊ Telegram: {e}")
        return False


async def main():
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"рџ›‘ РџРѕР»СѓС‡РµРЅ СЃРёРіРЅР°Р» {signum}, Р·Р°РІРµСЂС€Р°РµРј...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    lock_file = "bot.lock"

    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                logger.error(f"вќЊ Р‘РѕС‚ СѓР¶Рµ Р·Р°РїСѓС‰РµРЅ (PID {old_pid})! РЈРґР°Р»РёС‚Рµ bot.lock РµСЃР»Рё СЌС‚Рѕ РѕС€РёР±РєР°.")
                return
            else:
                logger.warning(f"вљ пёЏ РќР°Р№РґРµРЅ СѓСЃС‚Р°СЂРµРІС€РёР№ lock (PID {old_pid} РЅРµ СЃСѓС‰РµСЃС‚РІСѓРµС‚), СѓРґР°Р»СЏСЋ...")
                os.remove(lock_file)
        except Exception:
            logger.warning("вљ пёЏ РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕС‡РёС‚Р°С‚СЊ bot.lock, СѓРґР°Р»СЏСЋ Рё РїСЂРѕРґРѕР»Р¶Р°СЋ...")
            os.remove(lock_file)

    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))

    logger.info("=" * 60)
    logger.info("рџљЂ Р‘Р›РћРљРР РћР’РљР + AI (РїСЂРѕСЃС‚РѕР№ РїРµСЂРµСЃРєР°Р· РЅРѕРІРѕСЃС‚РµР№)")
    logger.info("=" * 60)

    posted = None

    try:
        init_clients()

        if not await check_telegram_connection():
            logger.error("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє Telegram. РџСЂРѕРІРµСЂСЊС‚Рµ С‚РѕРєРµРЅ Рё СЃРµС‚СЊ.")
            return

        posted = PostedManager(config.db_file)

        if posted.verify_db():
            logger.info("вњ… Р‘Р” OK")
        else:
            logger.error("вќЊ РџСЂРѕР±Р»РµРјР° СЃ Р‘Р”!")
            return

        posted.cleanup(config.retention_days)

        stats = posted.get_stats()
        logger.info(f"рџ“Љ РЎС‚Р°С‚РёСЃС‚РёРєР°: {stats['total_posted']} posted, {stats['total_rejected']} РІ С‡С‘СЂРЅРѕРј СЃРїРёСЃРєРµ")

        recent = posted.get_recent_posts(config.rotation_history_size)
        if recent:
            logger.info(f"рџ“‹ РџРѕСЃР»РµРґРЅРёРµ {len(recent)} РїРѕСЃС‚РѕРІ:")
            for p in recent:
                logger.info(f"   вЂў [{p['topic']}][{p.get('source', '?')}] {p['title'][:50]}...")

        if shutdown_event.is_set():
            logger.info("рџ›‘ РџСЂРµСЂС‹РІР°РЅРёРµ РїРµСЂРµРґ Р·Р°РіСЂСѓР·РєРѕР№ RSS")
            return

        raw = await load_all_feeds()

        sources_count: Dict[str, int] = {}
        for art in raw:
            sources_count[art.source] = sources_count.get(art.source, 0) + 1
        logger.info(f"рџ“° РСЃС‚РѕС‡РЅРёРєРё: {sources_count}")

        working = sum(1 for v in sources_count.values() if v > 0)
        logger.info(f"рџ“Ў Р Р°Р±РѕС‚Р°СЋС‰РёС… РёСЃС‚РѕС‡РЅРёРєРѕРІ: {working}/{len(RSS_FEEDS)}")

        if shutdown_event.is_set():
            logger.info("рџ›‘ РџСЂРµСЂС‹РІР°РЅРёРµ РїРµСЂРµРґ С„РёР»СЊС‚СЂР°С†РёРµР№")
            return

        candidates = filter_and_dedupe(raw, posted)

        if not candidates:
            logger.info("рџ“­ РќРµС‚ РїРѕРґС…РѕРґСЏС‰РёС… РЅРѕРІРѕСЃС‚РµР№. Р—Р°РІРµСЂС€Р°РµРј СЂР°Р±РѕС‚Сѓ.")
            return

        candidates = rotate_candidates(candidates, posted)

        logger.info("рџЋЇ РўРѕРї-10 РєР°РЅРґРёРґР°С‚РѕРІ РїРѕСЃР»Рµ СЂРѕС‚Р°С†РёРё:")
        for i, c in enumerate(candidates[:10]):
            topic_t = Topic.detect(f"{c.title} {c.summary}")
            logger.info(f"  {i+1}. [{topic_t}] [{c.source}] {c.title[:55]}")

        published = False
        for article in candidates[:25]:
            if shutdown_event.is_set():
                logger.info("рџ›‘ РџСЂРµСЂС‹РІР°РЅРёРµ РІ С†РёРєР»Рµ РїСѓР±Р»РёРєР°С†РёРё")
                break

            dup_result = posted.is_duplicate(article.link, article.title, article.summary)
            if dup_result.is_duplicate:
                posted.log_rejected(article, f"FINAL_DUP: {'; '.join(dup_result.reasons[:2])}")
                continue

            summary = await generate_summary(article)
            if not summary:
                posted.log_rejected(article, "GENERATION_FAILED")
                continue

            if await post_article(article, summary, posted):
                logger.info("рџЏЃ Р“РѕС‚РѕРІРѕ!")
                published = True
                break

            await asyncio.sleep(2)

        if not published:
            logger.info("рџ” РќРµ СѓРґР°Р»РѕСЃСЊ РѕРїСѓР±Р»РёРєРѕРІР°С‚СЊ РЅРё РѕРґРЅСѓ СЃС‚Р°С‚СЊСЋ.")

    except asyncio.CancelledError:
        logger.info("рџ›‘ РћРїРµСЂР°С†РёСЏ РѕС‚РјРµРЅРµРЅР°")
    except Exception as e:
        logger.error(f"вќЊ РљСЂРёС‚РёС‡РµСЃРєР°СЏ РѕС€РёР±РєР°: {e}", exc_info=True)
    finally:
        if posted:
            posted.close()
        if bot:
            try:
                await bot.session.close()
                logger.info("рџ”’ Telegram СЃРµСЃСЃРёСЏ Р·Р°РєСЂС‹С‚Р°")
            except Exception as e:
                logger.error(f"вќЊ РћС€РёР±РєР° Р·Р°РєСЂС‹С‚РёСЏ Telegram: {e}")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        logger.info("рџ‘‹ Р—Р°РІРµСЂС€РµРЅРёРµ СЂР°Р±РѕС‚С‹")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("рџ›‘ РџСЂРµСЂРІР°РЅРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»РµРј")
    except Exception as e:
        logger.error(f"вќЊ Р¤Р°С‚Р°Р»СЊРЅР°СЏ РѕС€РёР±РєР°: {e}", exc_info=True)
        sys.exit(1)






























































































































































































































































