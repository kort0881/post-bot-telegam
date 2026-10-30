"""Модели данных: статья из RSS, тема, редакционный формат."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Полный текст статьи, если его удалось получить (ТЗ §4)
    full_text: Optional[str] = None

    @property
    def text(self) -> str:
        return f"{self.title} {self.summary}"

    def age_hours(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        published = self.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return (now - published).total_seconds() / 3600


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
        LLM: "#ChatGPT #LLM #OpenAI #нейросети",
        IMAGE_GEN: "#Midjourney #StableDiffusion #ИИАрт",
        ROBOTICS: "#роботы #робототехника #автоматизация",
        HARDWARE: "#NVIDIA #чипы #GPU",
        MESSENGER: "#Telegram #мессенджеры #боты",
        GENERAL: "#ИИ #технологии #AI",
        BLOCK: "#РКН #блокировки #цензура",
        BYPASS: "#блокировки #цензура",
        WHITELIST: "#белыйсписок #доступность",
    }

    # Короткий набор хештегов для публикации (ТЗ §8: 1–3 релевантных)
    SHORT_HASHTAGS = {
        LLM: ["#нейросети", "#LLM"],
        IMAGE_GEN: ["#ИИАрт", "#нейросети"],
        ROBOTICS: ["#робототехника"],
        HARDWARE: ["#железо"],
        MESSENGER: ["#мессенджеры"],
        GENERAL: ["#технологии"],
        BLOCK: ["#блокировки", "#РКН"],
        BYPASS: ["#обходблокировок"],
        WHITELIST: ["#белыйсписок"],
    }

    @staticmethod
    def detect(text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["блокировк", "ркн", "roskomnadzor", "заблокирован", "реестр"]):
            return Topic.BLOCK
        if any(x in t for x in ["vless", "v2ray", "xray", "wireguard", "обход", "dpi", "антизапрет"]):
            return Topic.BYPASS
        if any(x in t for x in ["белый список", "whitelist", "доступность сайта"]):
            return Topic.WHITELIST
        if any(x in t for x in ["telegram", "телеграм", "мессенджер", "durov"]):
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

    @classmethod
    def hashtags(cls, topic: str, limit: int = 3) -> str:
        tags = cls.SHORT_HASHTAGS.get(topic) or cls.SHORT_HASHTAGS[cls.GENERAL]
        return " ".join(tags[:limit])


class PostFormat:
    """Пять редакционных форматов (ТЗ §5)."""

    HOWTO = "howto"              # практическое решение, 30%
    EXPERIMENT = "experiment"    # эксперимент и проверка, 25%
    CONSEQUENCE = "consequence"  # последствия новости, 20%
    ANALYSIS = "analysis"        # разбор и расследование, 15%
    TOOL = "tool"               # инструменты и находки, 10%

    ALL = [HOWTO, EXPERIMENT, CONSEQUENCE, ANALYSIS, TOOL]

    TARGET_SHARE = {
        HOWTO: 0.30,
        EXPERIMENT: 0.25,
        CONSEQUENCE: 0.20,
        ANALYSIS: 0.15,
        TOOL: 0.10,
    }

    TITLES = {
        HOWTO: "Практическое решение",
        EXPERIMENT: "Эксперимент и проверка",
        CONSEQUENCE: "Последствия новости",
        ANALYSIS: "Разбор",
        TOOL: "Инструменты и находки",
    }

    # Рубрики для узнаваемого оформления (ТЗ §6.5)
    RUBRIC = {
        HOWTO: "Что делать",
        EXPERIMENT: "Проверка недели",
        CONSEQUENCE: "Что изменилось",
        ANALYSIS: "Разбор блокировки",
        TOOL: "Полезная находка",
    }
