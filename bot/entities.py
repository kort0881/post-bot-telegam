"""Субъекты материалов: одна компания или человек — один сюжет.

Зачем отдельно от Topic: тема («llm», «hardware») слишком крупная. Nvidia попадает
в hardware, Claude в llm, поэтому лимиты тем пропускали оба сюжета, и канал выдавал
по 4 материала про Anthropic и столько же про Хуанга за четыре дня.

Здесь субъект определяется по словарю алиасов: латинские имена ищутся по границам
слова, русские — с падежными окончаниями (хуанг → уход Хуанга). Список намеренно
узкий: только те, кто реально даёт повторяющиеся новости. Незнакомые имена не
считаются субъектами — иначе кулдаун начнёт резать случайные материалы.
"""
import re
from functools import lru_cache
from typing import Dict, List, Pattern, Tuple

# canonical -> алиасы (нижний регистр). Кириллица матчится с окончаниями.
ENTITY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "nvidia": ("nvidia", "хуанг", "huang", "geforce", "cuda"),
    "anthropic": ("anthropic", "антропик", "claude", "клод", "fable", "mythos"),
    "openai": ("openai", "chatgpt", "чатгпт", "альтман", "altman", "sora"),
    "google": ("google", "гугл", "gemini", "гемини", "deepmind"),
    "microsoft": ("microsoft", "майкрософт", "copilot", "копилот"),
    "apple": ("apple", "эппл", "iphone", "айфон", "siri", "сири"),
    "meta": ("meta platforms", "meta ai", "llama", "лама", "zuckerberg", "цукерберг"),
    "amazon": ("amazon", "амазон", "alexa", "алекса"),
    "amd": ("amd", "radeon", "радеон"),
    "intel": ("intel", "интел"),
    "deepseek": ("deepseek", "дипсик"),
    "xai": ("xai", "grok", "грок", "илон маск"),
    "qwen": ("qwen", "квен", "alibaba", "алибаба"),
    "huawei": ("huawei", "хуавей", "ascend"),
    "samsung": ("samsung", "самсунг"),
    "softbank": ("softbank", "софтбанк"),
    "mistral": ("mistral", "мистраль"),
    "baidu": ("baidu", "байду"),
    "tencent": ("tencent", "тенсент"),
    "bytedance": ("bytedance", "tiktok", "тикток"),
    "rkn": ("роскомнадзор", "ркн", "roskomnadzor", "rkn"),
    "mintsifry": ("минцифры", "mintsifry", "mincifry"),
    "telegram": ("telegram", "телеграм", "дуров", "durov"),
}

def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    if re.search(r"[а-яё]", alias):
        # русские алиасы — с падежными окончаниями: хуанг → «уход Хуанга»
        return rf"(?<![а-яё]){escaped}[а-яё]{{0,3}}(?![а-яё])"
    return rf"(?<![a-z0-9]){escaped}(?:['’]s)?(?![a-z0-9])"


@lru_cache(maxsize=1)
def _compiled() -> Tuple[Pattern, List[str]]:
    names = list(ENTITY_ALIASES)
    groups = []
    for index, name in enumerate(names):
        alternatives = "|".join(_alias_pattern(alias) for alias in ENTITY_ALIASES[name])
        groups.append(f"(?P<e{index}>{alternatives})")
    return re.compile("|".join(groups)), names


def extract_entities(text: str) -> List[str]:
    """Субъекты материала в порядке первого упоминания. Пустой список — норма."""
    if not text:
        return []
    pattern, names = _compiled()
    found: List[str] = []
    for match in pattern.finditer(text.lower()):
        name = names[int(match.lastgroup[1:])]
        if name not in found:
            found.append(name)
    return found


def describe(entities: List[str]) -> str:
    """Человекочитаемая строка для логов вида «nvidia, anthropic»."""
    return ", ".join(entities)


__all__ = ["ENTITY_ALIASES", "describe", "extract_entities"]
