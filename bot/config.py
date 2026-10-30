"""Конфигурация бота: окружение, фиды, ключевые слова, лимиты.

Значения читаются из переменных окружения там, где это имеет смысл (см. .env.example).
Константы ключевых слов и источников — часть редакционной стратегии, а не окружения.
"""
import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# ===== МОДЕЛИ GROQ =====
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "groq/compound",
    "qwen/qwen3.6-27b",
]

GROQ_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
]

# ===== RSS =====
# PRIMARY_SOURCES больше не даёт абсолютного приоритета: используется только
# как небольшая надбавка за надёжность источника в едином рейтинге.
PRIMARY_SOURCES = {
    "3DNews Hardware",
    "3DNews Software",
}

RSS_FEEDS = [
    ("https://3dnews.ru/hardware-news/rss", "3DNews Hardware"),
    ("https://3dnews.ru/software-news/rss", "3DNews Software"),
    ("https://roskomsvoboda.org/feed/", "Роскомсвобода"),
    ("https://rkn.gov.ru/rss/news.xml", "РКН"),
    ("https://www.comnews.ru/rss/news", "ComNews"),
    ("https://news.google.com/rss/search?q=блокировка+РКН+VPN+россия&hl=ru&gl=RU&ceid=RU:ru", "Google News (Блокировки)"),
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

# ===== КЛЮЧЕВЫЕ СЛОВА =====
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
    "нейросеть", "нейросети", "искусственный интеллект",
    "машинное обучение", "генеративный ии",
]

AI_KEYWORDS_WEAK = [
    "ai", "nvidia", "copilot", "generative",
    "multimodal", "reasoning", "inference", "embedding",
    "robotics", "humanoid", "automation",
    "nlp", "ai model", "ai training",
    "бот", "боты", "автоматизация",
    "нейро", "ии",
]

BLOCK_KEYWORDS = [
    "блокировка", "заблокирован", "реестр ркн", "roskomnadzor", "rkn",
    "обход блокировок", "dpi", "замедление трафика", "sniffing",
    "vless", "v2ray", "xray", "wireguard", "openvpn", "amnezia",
    "белый список", "whitelist", "прокси", "туннелирование",
    "utls", "fragment", "антизапрет", "antizapret",
    "замедление youtube", "замедление ютуб",
]

# Профильные темы канала: блокировки, VPN, прокси, сети, безопасность, протоколы.
# Материал с такими словами проходит профильную проверку даже при наличии
# «обзор», «тест», «review» и т.п.
PROFILE_KEYWORDS = BLOCK_KEYWORDS + [
    "vpn", "прокси-сервер", "proxy", "shadowsocks", "trojan", "reality",
    "xhttp", "hysteria", "sing-box", "clash", "karing", "v2rayn", "happ",
    "протокол", "шифрование", "tls", "sni", "dns", "doh", "dot",
    "маршрутизац", "файрвол", "firewall", "nftables", "iptables",
    "утечка ip", "утечка данных", "цензура", "роскомсвобода", "ркн",
    "blocking", "censorship", "circumvent", "deep packet inspection",
    "интернет-доступ", "доступ к интернету", "обход",
]

GAMES_EXCLUDE = [
    "ps5", "xbox", "nintendo", "game review", "baldur's gate", "roblox", "esports",
    "twitch streamer", "fortnite", "игра", "игровая", "гейминг", "киберспорт",
]

BUSINESS_EXCLUDE = [
    "steps down", "resigns", "fired", "laid off", "layoffs", "new ceo", "new cto",
    "promoted to", "departing", "leaves company", "board meeting", "shareholder",
    "quarterly earnings", "earnings call", "revenue report", "stock price", "ipo",
    "merger", "acquisition", "lawsuit filed", "sued by", "legal battle",
    "уходит", "уволен", "увольнение", "сокращение", "назначен", "покидает",
    "совет директоров", "акционеры", "квартальный отчёт", "выручка", "капитализация",
    "слияние", "поглощение", "судебный иск",
]

PROMO_PATTERNS = [
    "newsletter", "рассылка", "подпишитесь", "subscribe", "sign up",
    "free trial", "скидка на подписку", "вебинар", "webinar", "buy now", "special offer",
    "купить vpn", "vpn сервис", "тариф", "промокод",
]

# Признаки коммерческого материала: именно они вместе с review-словами
# отсекают рекламные подборки, а не сами «обзор»/«тест».
# Основы слов: «скидк» ловит «скидка», «скидки», «скидкой» — но не «скидка» в
# составе другого слова. Регистр не важен.
COMMERCIAL_MARKERS = [
    "скидк", "промокод", "купон", "распродаж", "дисконт", "coupon",
    "deal", "sale", "buy now", "купить", "заказать", "ценник", "цена", "цены",
    "цену", "тариф", "подписка за", "best vpn", "top vpn", "рейтинг vpn", "выгодн",
]

REVIEW_KEYWORDS = ["review", "tested", "hands-on", "обзор", "тест", "скидка", "discount", "deal", "best", "top 10"]

# Вакансии описываются точными фразами, а не словом «работа»: из-за подстрочного
# поиска отбрасывались «работает», «заработал», «работоспособность».
JUNK_PATTERNS = [
    "ваканси", "открыта вакансия", "открыта позиция",
    "ищем сотрудника", "ищем менеджера", "ищем разработчика", "ищем специалиста",
    "ищет менеджера", "ищет сотрудника", "ищет разработчика",
    "требуется менеджер", "требуется специалист", "требуется разработчик",
    "резюме отправлять", "присылайте резюме", "резюме на почту",
    "набирает сотрудников", "нанимает сотрудник", "рекрутинг", "hh.ru",
    "we are hiring", "hiring now", "job opening", "job vacancy",
]

RUSSIA_KEYWORDS = ["россия", "рф", "минц", "госдума", "путин", "москва", "санкт-петербург", "совет федерации", "кремль", "правительство рф", "роскомнадзор", "ркн"]

# ===== ЛИМИТЫ ДЛИНЫ ПО ФОРМАТАМ (символы) =====
# Единый источник правды: раньше промпт требовал 700–1000, а валидатор — минимум 700,
# из-за чего короткие новости (350–600) физически не могли выйти.
POST_LENGTH_LIMITS = {
    "news": (350, 600),
    "howto": (700, 1500),
    "experiment": (350, 2500),
    "analysis": (1200, 2500),
    "consequence": (500, 1200),
    "tool": (400, 1200),
}


class Config:
    """Настройки запуска. Ничего не падает на импорте: проверка — в validate()."""

    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.channel_id = os.getenv("CHANNEL_ID")
        self.retention_days = _int_env("RETENTION_DAYS", 90)
        self.db_file = os.getenv("DB_FILE") or "posted_articles.db"

        self.title_similarity_threshold = _float_env("TITLE_SIMILARITY", 0.60)
        self.ngram_similarity_threshold = _float_env("NGRAM_SIMILARITY", 0.55)
        self.jaccard_threshold = _float_env("JACCARD_SIMILARITY", 0.55)
        self.same_domain_similarity = _float_env("SAME_DOMAIN_SIMILARITY", 0.65)

        self.subject_window_hours = _int_env("SUBJECT_WINDOW_HOURS", 48)
        self.max_posts_per_subject = _int_env("MAX_POSTS_PER_SUBJECT", 10)
        self.subject_min_interval_hours = _int_env("SUBJECT_MIN_INTERVAL_HOURS", 1)
        self.same_subject_similarity_threshold = _float_env("SAME_SUBJECT_SIMILARITY", 0.70)

        # Длина поста: минимум общий, но не выше нижней границы самого короткого
        # формата — иначе короткие новости не публикуются (см. POST_LENGTH_LIMITS).
        self.min_post_length = _int_env("MIN_POST_LENGTH", POST_LENGTH_LIMITS["news"][0])
        self.max_article_age_hours = _int_env("MAX_ARTICLE_AGE_HOURS", 720)
        self.min_ai_score = _int_env("MIN_AI_SCORE", 1)
        self.max_repeat_sentences = _int_env("MAX_REPEAT_SENTENCES", 2)

        self.diversity_window = _int_env("DIVERSITY_WINDOW", 8)
        self.same_topic_limit = _int_env("SAME_TOPIC_LIMIT", 4)

        self.rotation_history_size = _int_env("ROTATION_HISTORY_SIZE", 10)
        self.rotation_max_per_source = _int_env("ROTATION_MAX_PER_SOURCE", 6)

        self.source_min_posts_between = _int_env("SOURCE_MIN_POSTS_BETWEEN", 1)
        self.source_max_in_window = _int_env("SOURCE_MAX_IN_WINDOW", 6)

        self.batch_subject_limit = _int_env("BATCH_SUBJECT_LIMIT", 10)

        # Ограничения на серию публикаций подряд (ТЗ §12)
        self.max_same_format_in_row = _int_env("MAX_SAME_FORMAT_IN_ROW", 2)
        self.max_same_topic_in_row = _int_env("MAX_SAME_TOPIC_IN_ROW", 2)

        # Рейтинг: минимальный балл для публикации (ТЗ §3)
        self.publish_min_score = _int_env("PUBLISH_MIN_SCORE", 65)
        self.queue_min_score = _int_env("QUEUE_MIN_SCORE", 50)

        # Сколько кандидатов максимум проверять генерацией за прогон
        self.max_generation_attempts = _int_env("MAX_GENERATION_ATTEMPTS", 25)

        self.groq_retries_per_model = _int_env("GROQ_RETRIES_PER_MODEL", 2)
        self.groq_base_delay = _float_env("GROQ_BASE_DELAY", 2.0)
        self.telegram_timeout = _int_env("TELEGRAM_TIMEOUT", 30)
        self.http_timeout = _int_env("HTTP_TIMEOUT", 60)

        # Режим работы: auto | approval | collect (ТЗ §11)
        self.posting_mode = os.getenv("POSTING_MODE") or "auto"

    def missing_env(self):
        missing = []
        for value, name in [(self.groq_api_key, "GROQ_API_KEY"),
                            (self.telegram_token, "TELEGRAM_BOT_TOKEN"),
                            (self.channel_id, "CHANNEL_ID")]:
            if not value:
                missing.append(name)
        return missing

    def validate(self):
        missing = self.missing_env()
        if missing:
            raise SystemExit(f"❌ Отсутствуют: {', '.join(missing)}")


config = Config()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
