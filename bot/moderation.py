"""Автомодерация группы обсуждений канала «Доступ к интернету» (@vlessvpnvmess).

Задача: удалять явный спам и скам, не трогая живую дискуссию. Решения
принимаются эвристиками (бесплатно, мгновенно), LLM не требуется. Всё, что
удалено или забанено, пишется в SQLite; владельцу ничего не приходит — он
смотрит `/modlog` в личке бота.

Правила по стоп-словам (18.11.2026, слово владельца): режем рекламу, «заработок»,
продажу ключей/конфигов/подписок, казино/ставки/займы, инвайты в чужие каналы.
Не режем: вопросы про настройку, ссылки на свои домены, скриншоты ошибок.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

# --- домены, которые резать нельзя: свои и профильные -----------------------
WHITELIST_DOMAINS = (
    "dostupnety.website",
    "rwr-2.top",
    "vlesstrojan",
    "github.com/kort0881",
    "telegra.ph",
    "wikipedia.org",
)

# --- признаки спама (регулярки по нижнему регистру) -------------------------
# HIGH_RISK — прямое нарушение: продажа доступа, инвайты, азартные игры. За них бан.
HIGH_RISK_PATTERNS = (
    r"продам.{0,20}(?:ключ|vless|подписк|конфиг|аккаунт|доступ)",
    r"куплю.{0,20}(?:ключ|vless|подписк|конфиг)",
    r"vless.{0,15}(?:бесплатн|навсегда|на год|на 10 лет|за 100)",
    r"казино",
    r"ставки на спорт",
    r"1xbet",
    r"букмекер",
)

# MILD — реклама, «заработок», кредиты. Сами по себе режутся только со ссылкой.
MILD_PATTERNS = (
    r"зарабо(?:т|та)ть",
    r"заработок",
    r"инвест(?:иц|ор|ируй)",
    r"крипт.{0,20}(?:выплат|обмен|бот|сигнал)",
    r"обменник.{0,10}(?:валют|крипт)",
    r"займ.{0,20}без отказ",
    r"кредит.{0,20}онлайн",
    r"порно",
    r"подпишись.{0,25}канал",
    r"переходи.{0,15}ссылк",
    r"пиши.{0,10}в лс.{0,25}(?:зарабо|деньг|доход|подработ)",
    r"подработ.{0,20}(?:онлайн|без опыт)",
    r"выведу.{0,15}(?:деньг|средств)",
    r"гарантированн.{0,10}доход",
)

SPAM_PATTERNS = HIGH_RISK_PATTERNS + MILD_PATTERNS

# Ссылки и упоминания
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
INVITE_RE = re.compile(r"(?:t\.me/(?:\+|joinchat/)|telegram\.me/(?:\+|joinchat/))", re.IGNORECASE)
MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{4,32})")

MAX_TEXT_FOR_LOG = 500


@dataclass
class Verdict:
    """Решение по сообщению: action — None / "delete" / "delete_ban"."""

    action: Optional[str] = None
    reason: str = ""
    score: int = 0

    @property
    def is_spam(self) -> bool:
        return self.action in ("delete", "delete_ban")


def _lower(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _domain_allowed(url: str) -> bool:
    low = url.lower()
    return any(domain in low for domain in WHITELIST_DOMAINS)


def extract_links(text: str) -> List[str]:
    """Внешние ссылки и инвайты; свои домены отбрасываются."""
    links = [m.group(0) for m in URL_RE.finditer(text or "") if not _domain_allowed(m.group(0))]
    invites = INVITE_RE.findall(text or "")
    if invites:
        links.append(invites[0])
    return links


def count_pattern_hits(text: str) -> List[str]:
    low = _lower(text)
    return [pattern for pattern in SPAM_PATTERNS if re.search(pattern, low)]


def count_high_risk(text: str) -> List[str]:
    low = _lower(text)
    return [pattern for pattern in HIGH_RISK_PATTERNS if re.search(pattern, low)]


def classify(text: str, has_media: bool = False, sender_is_new: bool = False) -> Verdict:
    """Оценить сообщение. Чистая функция: без сети и БД, поэтому тестируется напрямую."""
    text = text or ""
    links = extract_links(text)
    hits = count_pattern_hits(text)
    high = count_high_risk(text)

    mild = [pattern for pattern in hits if pattern not in high]
    score = 3 * len(high) + 2 * len(mild)
    reasons = []
    if high:
        reasons.append("нарушение: " + ", ".join(high[:3]))
    if mild:
        reasons.append("реклама: " + ", ".join(mild[:3]))
    if links:
        score += 1
        reasons.append(f"ссылки: {len(links)}")

    # Инвайт в чужой чат — самостоятельный признак, даже без стоп-слов
    if INVITE_RE.search(text):
        score += 3
        reasons.append("инвайт в чужой чат")

    # Новый участник сразу с внешней ссылкой и без вопроса — типичный заход рекламщика
    low = _lower(text)
    if links and sender_is_new and "?" not in text and len(low) < 200:
        score += 1
        reasons.append("новый участник со ссылкой")

    reason = "; ".join(reasons) or "чисто"

    if score >= 3:
        return Verdict("delete_ban", reason, score)
    if score >= 2:
        return Verdict("delete", reason, score)
    return Verdict(None, reason, score)


def text_hash(text: str) -> str:
    return hashlib.sha1(_lower(text).strip().encode("utf-8", "ignore")).hexdigest()[:16]


class ModerationStore:
    """Лог удалений и счётчик повторов: SQLite, один файл рядом с сервисом."""

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mod_log (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts TEXT NOT NULL, chat_id INTEGER, message_id INTEGER,
                   user_id INTEGER, username TEXT, action TEXT, reason TEXT, text TEXT)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mod_seen (
                   hash TEXT, user_id INTEGER, ts REAL)"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_seen ON mod_seen(hash, ts)")
        self._conn.commit()

    def log(self, chat_id: int, message_id: int, user_id: Optional[int], username: str,
            action: str, reason: str, text: str) -> None:
        self._conn.execute(
            "INSERT INTO mod_log (ts, chat_id, message_id, user_id, username, action, reason, text)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), chat_id, message_id,
             user_id, username or "", action, reason, (text or "")[:MAX_TEXT_FOR_LOG]),
        )
        self._conn.commit()

    def seen_before(self, text: str, user_id: Optional[int], window_sec: int = 600) -> int:
        """Сколько раз этот же текст от этого же автора встречался за окно."""
        h = text_hash(text)
        cutoff = time.time() - window_sec
        count = self._conn.execute(
            "SELECT COUNT(*) FROM mod_seen WHERE hash=? AND user_id IS ? AND ts>?",
            (h, user_id, cutoff),
        ).fetchone()[0]
        self._conn.execute("INSERT INTO mod_seen (hash, user_id, ts) VALUES (?,?,?)",
                           (h, user_id, time.time()))
        self._conn.execute("DELETE FROM mod_seen WHERE ts<?", (time.time() - 86400,))
        self._conn.commit()
        return int(count)

    def tail(self, limit: int = 10) -> List[dict]:
        rows = self._conn.execute(
            "SELECT ts, user_id, username, action, reason, text FROM mod_log"
            " ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [
            {"ts": r[0], "user_id": r[1], "username": r[2], "action": r[3],
             "reason": r[4], "text": r[5]}
            for r in rows
        ]

    def stats(self, days: int = 7) -> dict:
        cutoff = time.time() - days * 86400
        since = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows = self._conn.execute(
            "SELECT action, COUNT(*) FROM mod_log WHERE ts>? GROUP BY action", (since,)
        ).fetchall()
        return {action: count for action, count in rows}

    def close(self) -> None:
        self._conn.close()


def render_log(entries: Sequence[dict]) -> str:
    if not entries:
        return "🗂️ Лог модерации пуст."
    lines = ["🗂️ Последние решения модерации:"]
    for item in entries:
        who = f"@{item['username']}" if item["username"] else str(item["user_id"])
        lines.append(f"{item['ts']} · {item['action']} · {who} · {item['reason']}"
                     f"\n   «{(item['text'] or '')[:120]}»")
    return "\n".join(lines)


def render_stats(stats: dict) -> str:
    if not stats:
        return "📊 За неделю удалений не было."
    total = sum(stats.values())
    parts = ", ".join(f"{action}: {count}" for action, count in sorted(stats.items()))
    return f"📊 За неделю: {total} решений ({parts})."


def self_check() -> dict:
    """Диагностика окружения: что настроено, куда пишем."""
    return {
        "discussion_chat_id": os.getenv("DISCUSSION_CHAT_ID", ""),
        "owner_id": os.getenv("OWNER_ID", ""),
        "mod_db": os.getenv("MOD_DB", "/opt/post-bot-mod/moderation.db"),
        "patterns": len(SPAM_PATTERNS),
        "whitelist": list(WHITELIST_DOMAINS),
    }


if __name__ == "__main__":  # быстрый прогон без Telegram
    samples = [
        "Продам VLESS ключи, 200р месяц, пиши в лс",
        "Заработок от 3000 в день, подробности тут https://example.com/join",
        "Перестал подключаться HAPP после обновления, что смотреть?",
        "Вот актуальные ключи https://cdn.dostupnety.website/sub/abc",
        "t.me/+AbCdEfGhIj инвайт в нормальный чат по vless",
    ]
    for sample in samples:
        verdict = classify(sample)
        print(json.dumps({"text": sample[:40], "action": verdict.action,
                          "reason": verdict.reason, "score": verdict.score},
                         ensure_ascii=False))
