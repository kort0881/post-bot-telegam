"""Тесты аналитики (ТЗ §10): разбор метрик, окна, отчёт."""
from datetime import datetime, timedelta, timezone

from bot.analytics import (
    due_windows,
    effective_views,
    parse_page_metrics,
    parse_views,
    popular_topics,
    render_report,
    weekly_report,
)


def test_parse_views_variants():
    assert parse_views("1.2K") == 1200
    assert parse_views("23.4M") == 23400000
    assert parse_views("1 234") == 1234
    assert parse_views("987") == 987
    assert parse_views("1,5K") == 1500
    assert parse_views("—") is None
    assert parse_views(None) is None
    assert parse_views("") is None


def test_parse_page_metrics_reads_views_and_reactions():
    html = (
        '<span class="tgme_widget_message_views">12.5K</span>'
        '<span class="tgme_reaction">👍 3</span>'
        '<span class="tgme_reaction">🔥 1</span>'
    )
    metrics = parse_page_metrics(html)
    assert metrics["views"] == 12500
    assert metrics["reactions"] == 2
    # пересылки в разметке отсутствуют — метрика неизвестна, а не ноль
    assert metrics["forwards"] is None


def test_parse_page_metrics_empty_page():
    metrics = parse_page_metrics("")
    assert metrics == {"views": None, "forwards": None, "reactions": None, "comments": None}


def test_due_windows_only_after_elapsed_time():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    row = {"published_at": "2026-01-01 09:00:00",
           "views_1h": None, "views_6h": None, "views_24h": None}
    assert due_windows(row, now) == ["views_1h"]

    row["views_1h"] = 100
    assert due_windows(row, now) == []

    row["published_at"] = "2025-12-31 12:00:00"
    assert due_windows(row, now) == ["views_6h", "views_24h"]


def test_effective_views_prefers_longest_window():
    assert effective_views({"views_1h": 10, "views_6h": 50, "views_24h": 120}) == 120
    assert effective_views({"views_1h": 10, "views_6h": 50, "views_24h": None}) == 50
    assert effective_views({"views_1h": None, "views_6h": None, "views_24h": None}) is None


def _row(days_ago: float, topic: str, fmt: str, views, source="Habr AI"):
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "message_id": abs(hash((topic, fmt, days_ago, views))) % 100000,
        "title": "тест", "source": source, "topic": topic, "post_format": fmt,
        "rubric": "", "score": 70,
        "published_at": published.strftime("%Y-%m-%d %H:%M:%S"),
        "views_1h": views, "views_6h": views, "views_24h": views,
        "forwards_24h": None, "reactions_24h": None, "comments_24h": None,
    }


def test_weekly_report_finds_weak_format_and_recommends():
    rows = [
        _row(1, "bypass", "howto", 200),
        _row(2, "bypass", "howto", 220),
        _row(3, "bypass", "howto", 180),
        _row(4, "llm", "consequence", 60),
        _row(5, "llm", "consequence", 50),
        _row(6, "llm", "consequence", 70),
    ]
    report = weekly_report(rows, days=7)
    assert report["posts"] == 6
    assert report["measured"] == 6
    assert report["median_views"] == 125

    formats = {block["format"]: block for block in report["formats"]}
    assert formats["howto"]["rel"] > 1.0
    assert formats["consequence"]["rel"] < 0.85
    assert any("сократить долю «consequence»" in r for r in report["recommendations"])

    text = render_report(report)
    assert "Рекомендации" in text
    assert "howto" in text


def test_weekly_report_ignores_old_posts():
    report = weekly_report([_row(30, "bypass", "howto", 500)], days=7)
    assert report["posts"] == 0
    assert report["recommendations"]


def test_popular_topics_marks_above_median():
    rows = [
        _row(1, "bypass", "howto", 300),
        _row(2, "llm", "howto", 40),
        _row(3, "llm", "howto", 45),
    ]
    assert popular_topics(rows) == ["bypass"]
