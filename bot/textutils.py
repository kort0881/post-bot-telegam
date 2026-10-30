"""Работа с текстом: нормализация URL/заголовков, похожесть, поиск терминов.

Поиск терминов идёт регулярками по границам слова, а не подстрокой: именно
подстрочный поиск превращал «работает» в вакансию «работа».
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, List, Optional, Pattern, Set, Tuple
from urllib.parse import parse_qs, urlencode

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid", "ref", "source", "mc_cid", "mc_eid", "yclid",
}


def normalize_url(url: str) -> str:
    try:
        u = url.lower().strip()
        u = u.replace("https://", "").replace("http://", "")
        u = u.replace("www.", "")
        if "?" in u:
            base, query_str = u.split("?", 1)
            params = parse_qs(query_str)
            clean = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
            if clean:
                u = f"{base}?{urlencode(clean, doseq=True)}"
            else:
                u = base
        return u.rstrip("/")
    except Exception:
        return (url.lower().strip()
                .replace("https://", "").replace("http://", "")
                .replace("www.", "").rstrip("/"))


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
        r'([a-zA-Zа-яА-ЯёЁ]+)\s*[-.]?\s*(\d+(?:\.\d+)?)',
        lambda m: m.group(1) + m.group(2).replace('.', ''),
        t
    )
    return t


STOP_WORDS = {
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
    'it', 'you', 'your', 'we', 'our', 'they', 'their', 'he', 'she',
    'him', 'her', 'his', 'hers', 'my', 'mine', 'yours', 'ours', 'theirs',
    'и', 'в', 'на', 'с', 'по', 'для', 'от', 'из', 'за', 'до', 'не',
    'что', 'как', 'это', 'все', 'его', 'она', 'они', 'мы', 'вы', 'он',
    'но', 'то', 'так', 'уже', 'или', 'ещё', 'еще', 'при', 'без',
    'тоже', 'также', 'будет', 'была', 'были', 'быть', 'может',
    'этот', 'эта', 'эти', 'тот', 'того', 'этого', 'свой', 'свои',
}


@lru_cache(maxsize=2000)
def get_title_words(title: str) -> frozenset:
    words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ0-9]+\b', title.lower())
    return frozenset(w for w in words if len(w) > 2 and w not in STOP_WORDS)


def get_sorted_word_signature(title: str) -> str:
    return ' '.join(sorted(get_title_words(title)))


def calculate_similarity(str1: str, str2: str) -> float:
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


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


# ===== ПОИСК ТЕРМИНОВ =====
# Термин ищется как основа слова: слева обязательна граница слова,
# справа граница не требуется (stem=True), чтобы «вакансия» находила «вакансию».
WORD_START = r'(?<![0-9a-zа-яё])'
WORD_END = r'(?![0-9a-zа-яё])'


def _term_to_pattern(term: str, stem: bool = True) -> str:
    term = term.strip().lower()
    escaped = r'\s+'.join(re.escape(part) for part in term.split())
    # Короткие односложные термины («ии», «ai», «vpn») ищем целиком,
    # иначе «ai» ловится внутри «aim», а «ии» — внутри других слов.
    short = stem and ' ' not in term and len(term) <= 3
    tail = WORD_END if (short or not stem) else ''
    return f'{WORD_START}{escaped}{tail}'


class TermMatcher:
    """Скомпилированный набор терминов: быстрый поиск + список совпадений."""

    def __init__(self, terms: Iterable[str], stem: bool = True, name: str = ""):
        self.name = name
        self.stem = stem
        self._terms: List[str] = [t for t in dict.fromkeys(t.strip().lower() for t in terms) if t]
        parts = [_term_to_pattern(t, stem=stem) for t in self._terms]
        self._regex: Optional[Pattern] = (
            re.compile('|'.join(f'(?P<t{i}>{p})' for i, p in enumerate(parts)))
            if parts else None
        )

    def matches(self, text: str) -> List[str]:
        if not self._regex or not text:
            return []
        found = []
        lowered = text.lower()
        for m in self._regex.finditer(lowered):
            idx = int(m.lastgroup[1:])
            found.append(self._terms[idx])
        # уникальные, в порядке первого появления
        return list(dict.fromkeys(found))

    def any(self, text: str) -> bool:
        return bool(self._regex and text and self._regex.search(text.lower()))

    def count(self, text: str) -> int:
        return len(self.matches(text))


def compile_terms(terms: Iterable[str], stem: bool = True, name: str = "") -> TermMatcher:
    return TermMatcher(terms, stem=stem, name=name)


# ===== ВАЛИДАЦИЯ ТЕКСТА ПОСТА =====
def count_sentences(text: str) -> int:
    parts = re.split(r'[.!?]+(?:\s+|$)', text.strip())
    return len([p for p in parts if p.strip()])


def has_repeated_sentences(text: str, max_repeats: int = 2) -> bool:
    sentences = re.split(r'[.!?]\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if len(sentences) < 2:
        return False
    repeat_count = 0
    checked = []
    for sent in sentences:
        for prev in checked:
            if calculate_similarity(sent, prev) > 0.6:
                repeat_count += 1
                if repeat_count >= max_repeats:
                    return True
        checked.append(sent)
    return False


SERVICE_LABELS = (
    r'(?:НОВОСТЬ|Заголовок|Содержание|Источник|ПОСТ|Суть|Значение|Вступление|'
    r'Последствия|Новость дня|Формат|Рубрика)'
)


def strip_service_lines(text: str) -> str:
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if re.match(rf'^{SERVICE_LABELS}\s*:?\s*', line.strip(), re.IGNORECASE):
            continue
        cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)
    for label in ("Заголовок", "Содержание", "Источник", "НОВОСТЬ", "Суть", "Значение",
                  "Вступление", "Последствия", "Новость дня", "Формат", "Рубрика"):
        text = re.sub(rf'\b{label}\s*:\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


BAD_TAIL_WORDS = (
    'и|в|на|с|по|для|от|из|за|до|не|что|как|это|все|его|она|они|мы|вы|он|но|то|так|уже|или|'
    'ещё|еще|при|без|тоже|также|будет|была|были|быть|может|этот|эта|эти|тот|того|этого|свой|свои'
)


def is_valid_post_text(
    text: Optional[str],
    min_len: int,
    max_len: Optional[int] = None,
) -> Tuple[bool, str]:
    if text is None:
        return False, "TEXT_NONE"

    cleaned = text.strip()

    if not cleaned:
        return False, "TEXT_EMPTY"

    if cleaned in {".", "...", "-", "—", ":"}:
        return False, "TEXT_PUNCT_ONLY"

    if cleaned.lower() in {"skip", "none", "null", "undefined"}:
        return False, "TEXT_SERVICE_VALUE"

    if len(cleaned) < min_len:
        return False, f"TEXT_TOO_SHORT ({len(cleaned)} < {min_len})"

    if max_len is not None and len(cleaned) > max_len:
        return False, f"TEXT_TOO_LONG ({len(cleaned)} > {max_len})"

    letters = sum(ch.isalpha() for ch in cleaned)
    if letters < 50:
        return False, f"TEXT_TOO_FEW_LETTERS ({letters})"

    sentence_count = count_sentences(cleaned)
    if sentence_count < 3:
        return False, f"TEXT_TOO_FEW_SENTENCES ({sentence_count})"

    if re.fullmatch(r'[\W_]+', cleaned, flags=re.UNICODE):
        return False, "TEXT_NON_ALNUM_ONLY"

    if re.search(rf'\b({BAD_TAIL_WORDS})\s*$', cleaned, re.IGNORECASE):
        return False, "TEXT_ENDS_BADLY"

    return True, "OK"


def clean_html(raw: str) -> str:
    """Грубая очистка HTML из RSS full-content."""
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', raw)
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#\d+;', ' ', text)
    return re.sub(r'[ \t]+', ' ', text).strip()
