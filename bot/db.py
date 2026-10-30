"""SQLite-состояние бота: опубликованное, отклонённое, статистика.

Схема совместима с существующей БД (posted_articles / rejected_urls):
все изменения — только аддитивные ALTER TABLE + CREATE TABLE IF NOT EXISTS,
поэтому файл posted_articles.db из репозитория продолжает работать.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .config import config
from .models import Article, Topic
from .textutils import (
    calculate_similarity,
    get_content_hash,
    get_domain,
    get_sorted_word_signature,
    get_title_words,
    ngram_similarity,
    normalize_title,
    normalize_url,
    parse_db_datetime,
    safe_json_loads,
)

logger = logging.getLogger(__name__)


class DuplicateCheckResult:
    def __init__(self):
        self.is_duplicate = False
        self.reasons: List[str] = []
        self.max_similarity = 0.0
        self.matched_title = ""

    def add_reason(self, reason: str, similarity: float = 0.0, matched: str = ""):
        self.reasons.append(reason)
        if similarity > self.max_similarity:
            self.max_similarity = similarity
            self.matched_title = matched
        self.is_duplicate = True


class PostedManager:
    def __init__(self, db_file: Optional[str] = None):
        self.db_file = db_file or config.db_file
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    # ===== подключение =====
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
            # Миграции: колонки добавляются по одной, отсутствие = норма
            for ddl in (
                "ALTER TABLE posted_articles ADD COLUMN subject TEXT DEFAULT 'other'",
                "ALTER TABLE posted_articles ADD COLUMN post_format TEXT",
                "ALTER TABLE posted_articles ADD COLUMN score INTEGER",
                "ALTER TABLE posted_articles ADD COLUMN message_id INTEGER",
                "ALTER TABLE rejected_urls ADD COLUMN source TEXT",
            ):
                try:
                    cursor.execute(ddl)
                except sqlite3.OperationalError:
                    pass

            indices = [
                ('idx_norm_url', 'posted_articles', 'norm_url'),
                ('idx_content_hash', 'posted_articles', 'content_hash'),
                ('idx_domain', 'posted_articles', 'domain'),
                ('idx_posted_date', 'posted_articles', 'posted_date'),
                ('idx_title_normalized', 'posted_articles', 'title_normalized'),
                ('idx_title_word_signature', 'posted_articles', 'title_word_signature'),
                ('idx_subject', 'posted_articles', 'subject'),
                ('idx_post_format', 'posted_articles', 'post_format'),
                ('idx_rejected_at', 'rejected_urls', 'rejected_at'),
            ]
            for idx_name, table, column in indices:
                try:
                    cursor.execute(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})')
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        logger.info("📚 База данных инициализирована")

    # ===== отклонённые материалы =====
    def log_rejected(self, article: Article, reason: str):
        """Сохранить причину отказа, чтобы потом можно было посмотреть, почему отсеяли."""
        logger.info(f"🚫 [{reason}]: {article.title[:60]}")
        with self._lock:
            try:
                conn = self._get_conn()
                conn.execute(
                    'INSERT INTO rejected_urls (norm_url, title, reason, rejected_at, source) '
                    'VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(norm_url) DO UPDATE SET reason=excluded.reason, '
                    'rejected_at=excluded.rejected_at',
                    (normalize_url(article.link), article.title[:300], reason[:300],
                     datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), article.source)
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"⚠️ Не удалось записать отказ: {e}")

    def get_rejection_reason(self, url: str) -> Tuple[bool, str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT reason, rejected_at FROM rejected_urls WHERE norm_url = ?',
                           (normalize_url(url),))
            row = cursor.fetchone()
            if row:
                return True, f"{row[0]} (отклонён {row[1]})"
            return False, ""

    def is_rejected(self, url: str) -> Tuple[bool, str]:
        return self.get_rejection_reason(url)

    # ===== история и лимиты =====
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
            return [{'title': r[0], 'date': r[1], 'normalized': r[2], 'entities': r[3]}
                    for r in cursor.fetchall()]

    def get_subject_stats_cached(self, hours: int = 24) -> Dict[str, List[dict]]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT subject, title, posted_date, title_normalized
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
                ORDER BY posted_date DESC
            ''', (f'-{hours} hours',))
            result: Dict[str, List[dict]] = {}
            for r in cursor.fetchall():
                result.setdefault(r[0], []).append(
                    {'title': r[1], 'date': r[2], 'normalized': r[3]}
                )
            return result

    def get_last_n_subjects(self, n: int = 5) -> List[str]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('SELECT subject FROM posted_articles ORDER BY posted_date DESC LIMIT ?', (n,))
            return [row[0] for row in cursor.fetchall()]

    def can_post_subject(self, subject: str) -> Tuple[bool, str]:
        return True, ""

    def check_subject_limit(self, subject: str, new_title: str,
                            new_entities: Optional[Set[str]] = None) -> Tuple[bool, str]:
        if subject in ("other", "", None):
            return True, ""

        recent_posts = self.get_subject_posts_in_window(subject, config.subject_window_hours)

        if len(recent_posts) >= config.max_posts_per_subject:
            return (False,
                    f"SUBJECT_LIMIT ({subject}: {len(recent_posts)}/"
                    f"{config.max_posts_per_subject} за {config.subject_window_hours}h)")

        if recent_posts:
            last_date = parse_db_datetime(recent_posts[0]['date'])
            hours_since = (datetime.now(timezone.utc) - last_date).total_seconds() / 3600
            if hours_since < config.subject_min_interval_hours:
                return (False,
                        f"SUBJECT_COOLDOWN ({subject}: {hours_since:.1f}h "
                        f"< {config.subject_min_interval_hours}h)")

        new_normalized = normalize_title(new_title)
        for post in recent_posts:
            sim = calculate_similarity(new_normalized, post['normalized'] or "")
            if sim > config.same_subject_similarity_threshold:
                return False, f"SUBJECT_SIMILAR ({subject}, sim={sim:.0%})"

        return True, ""

    # ===== дубли =====
    def is_duplicate(self, url: str, title: str, summary: str = "") -> DuplicateCheckResult:
        result = DuplicateCheckResult()
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            norm_url = normalize_url(url)
            title_normalized = normalize_title(title)
            title_words = set(get_title_words(title))
            content_hash = get_content_hash(f"{title} {summary}")
            domain = get_domain(url)
            window = f'-{config.retention_days} days'

            cursor.execute(
                'SELECT title FROM posted_articles WHERE norm_url = ? AND posted_date > datetime("now", ?)',
                (norm_url, window))
            row = cursor.fetchone()
            if row:
                result.add_reason("URL_EXACT", 1.0, row[0])
                return result

            if content_hash:
                cursor.execute(
                    'SELECT title FROM posted_articles WHERE content_hash = ? '
                    'AND posted_date > datetime("now", ?)',
                    (content_hash, window))
                row = cursor.fetchone()
                if row:
                    result.add_reason("CONTENT_HASH", 1.0, row[0])
                    return result

            cursor.execute(
                'SELECT title FROM posted_articles WHERE title_normalized = ? '
                'AND posted_date > datetime("now", ?)',
                (title_normalized, window))
            row = cursor.fetchone()
            if row:
                result.add_reason("TITLE_EXACT", 1.0, row[0])
                return result

            cursor.execute('''
                SELECT id, title, title_normalized, title_words, domain
                FROM posted_articles
                WHERE posted_date > datetime('now', ?)
            ''', (window,))

            for row in cursor.fetchall():
                existing_title = row[1]
                existing_normalized = row[2] or ""
                existing_words = set(safe_json_loads(row[3], []))
                existing_domain = row[4]

                seq_sim = calculate_similarity(title_normalized, existing_normalized)
                if seq_sim > config.title_similarity_threshold:
                    result.add_reason(f"TITLE_SIM ({seq_sim:.0%})", seq_sim, existing_title)

                ngram_sim = ngram_similarity(title, existing_title)
                if ngram_sim > config.ngram_similarity_threshold:
                    result.add_reason(f"NGRAM ({ngram_sim:.0%})", ngram_sim, existing_title)

                if existing_words:
                    jaccard = len(title_words & existing_words) / len(title_words | existing_words) \
                        if (title_words | existing_words) else 0.0
                    if jaccard > config.jaccard_threshold:
                        result.add_reason(f"JACCARD ({jaccard:.0%})", jaccard, existing_title)

                if domain == existing_domain and seq_sim > config.same_domain_similarity:
                    result.add_reason(f"SAME_DOMAIN ({seq_sim:.0%})", seq_sim, existing_title)

            return result

    def check_diversity(self, topic: str, source: str = "") -> Tuple[bool, str]:
        with self._lock:
            cursor = self._get_conn().cursor()

            if source:
                cursor.execute(
                    'SELECT source FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                    (config.rotation_history_size,))
                recent_sources = [row[0] for row in cursor.fetchall()]
                last_few = recent_sources[:config.source_min_posts_between]

                if source in last_few:
                    pos = last_few.index(source) + 1
                    return (False,
                            f"SOURCE_TOO_RECENT ({source} был {pos}-м из последних "
                            f"{config.source_min_posts_between})")

                source_count = sum(1 for s in recent_sources if s == source)
                if source_count >= config.source_max_in_window:
                    return (False,
                            f"SOURCE_LIMIT ({source}: {source_count}/{config.source_max_in_window} "
                            f"за последние {config.rotation_history_size})")

            if topic == Topic.GENERAL:
                return True, ""

            cursor.execute(
                'SELECT topic FROM posted_articles ORDER BY posted_date DESC LIMIT ?',
                (config.diversity_window,))
            recent_topics = [row[0] for row in cursor.fetchall()]
            if not recent_topics:
                return True, ""

            same_count = sum(1 for t in recent_topics if t == topic)
            if same_count >= config.same_topic_limit:
                return False, f"TOO_MANY: {same_count}/{config.diversity_window} = {topic}"

            return True, ""

    # ===== запись публикации =====
    def add(
        self,
        article: Article,
        topic: str = Topic.GENERAL,
        subject: str = "other",
        post_format: Optional[str] = None,
        score: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> bool:
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
                     title_word_signature, summary, content_hash, entities, topic, subject,
                     source, post_format, score, message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.link, norm_url, domain_val, article.title, title_normalized,
                    json.dumps(title_words), word_signature, (article.summary or "")[:1000],
                    content_hash, json.dumps([]), topic, subject, article.source,
                    post_format, score, message_id,
                ))
                conn.commit()
                logger.info(f"💾 Сохранено (topic={topic}, format={post_format}): {article.title[:50]}...")
                return True
            except sqlite3.IntegrityError:
                logger.warning(f"⚠️ Уже существует: {article.title[:40]}")
                return False
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения: {e}")
                return False

    def get_recent_posts(self, limit: int = 5) -> List[dict]:
        with self._lock:
            cursor = self._get_conn().cursor()
            cursor.execute('''
                SELECT title, topic, source, posted_date, subject, post_format
                FROM posted_articles
                ORDER BY posted_date DESC
                LIMIT ?
            ''', (limit,))
            return [{
                'title': r[0], 'topic': r[1], 'source': r[2],
                'date': r[3], 'subject': r[4] or 'other', 'format': r[5],
            } for r in cursor.fetchall()]

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
                "DELETE FROM posted_articles WHERE posted_date < datetime('now', ?)",
                (f'-{days} days',))
            deleted_posted = cursor.rowcount
            cursor.execute(
                "DELETE FROM rejected_urls WHERE rejected_at < datetime('now', ?)",
                (f'-{days} days',))
            deleted_rejected = cursor.rowcount
            conn.commit()
            logger.info(f"🧹 Очищено: {deleted_posted} posted, {deleted_rejected} rejected (старше {days} дней)")

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
                    logger.info("🔒 БД закрыта")
                except Exception as e:
                    logger.error(f"❌ Ошибка закрытия БД: {e}")
                finally:
                    self._local.conn = None
