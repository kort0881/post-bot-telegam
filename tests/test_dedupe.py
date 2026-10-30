import sqlite3
from datetime import datetime, timedelta, timezone

from bot.db import PostedManager
from bot.models import Article


def make(title, link, summary="", source="3DNews Software", age_hours=1):
    return Article(
        title=title,
        summary=summary,
        link=link,
        source=source,
        published=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


def test_same_news_from_two_sources_is_duplicate(db_file):
    posted = PostedManager(db_file)
    first = make("РКН внес Cloudflare в реестр блокировок", "https://roskomsvoboda.org/a/1")
    assert posted.add(first, "block", "block") is True

    second = make("РКН внес Cloudflare в реестр блокировок", "https://3dnews.ru/news/99")
    result = posted.is_duplicate(second.link, second.title, second.summary)
    assert result.is_duplicate
    assert result.reasons


def test_same_url_is_duplicate(db_file):
    posted = PostedManager(db_file)
    article = make("VLESS перестал работать у части пользователей", "https://example.com/x")
    posted.add(article)
    dup = posted.is_duplicate("https://example.com/x?utm_source=tg", article.title)
    assert dup.is_duplicate
    assert any("URL_EXACT" in r for r in dup.reasons)


def test_unrelated_news_not_duplicate(db_file):
    posted = PostedManager(db_file)
    posted.add(make("NVIDIA показала новый GPU", "https://3dnews.ru/a"))
    other = make("Роскомсвобода обновила инструкцию по обходу блокировок",
                 "https://roskomsvoboda.org/b")
    assert posted.is_duplicate(other.link, other.title) .is_duplicate is False


def test_rejection_reason_is_persisted(db_file):
    posted = PostedManager(db_file)
    article = make("Обзор: лучшие наушники года", "https://example.com/h")
    posted.log_rejected(article, "COMMERCIAL_ROUNDUP (обзор)")
    found, reason = posted.get_rejection_reason(article.link)
    assert found
    assert "COMMERCIAL_ROUNDUP" in reason


def test_schema_is_backward_compatible(db_file):
    posted = PostedManager(db_file)
    posted.close()
    conn = sqlite3.connect(db_file)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"posted_articles", "rejected_urls"} <= tables
    columns = {r[1] for r in conn.execute("PRAGMA table_info(posted_articles)")}
    assert {"url", "norm_url", "title", "subject", "source"} <= columns
    conn.close()


def test_cleanup_removes_old_rows_only(db_file):
    posted = PostedManager(db_file)
    posted.add(make("Свежая новость про DPI-блокировки", "https://example.com/fresh"))
    conn = posted._get_conn()
    conn.execute(
        "INSERT INTO posted_articles (url, norm_url, domain, title, title_normalized, posted_date) "
        "VALUES ('https://example.com/old','example.com/old','example.com','old','old', datetime('now','-200 days'))"
    )
    conn.commit()
    posted.cleanup(90)
    rows = conn.execute("SELECT COUNT(*) FROM posted_articles").fetchone()[0]
    assert rows == 1
