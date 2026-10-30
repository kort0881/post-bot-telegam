from datetime import datetime, timedelta, timezone

from bot.db import PostedManager
from bot.models import Article
from bot.pipeline import filter_and_dedupe, select_candidates


def make(title, source, summary="", age_hours=1, link=None):
    return Article(
        title=title,
        summary=summary,
        link=link or f"https://{abs(hash(title))}.example/n",
        source=source,
        published=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


def test_single_rating_across_all_sources(db_file):
    """ТЗ §2.3: кандидаты из всех источников участвуют в общем рейтинге."""
    posted = PostedManager(db_file)
    articles = [
        make("Роскомнадзор заблокировал Cloudflare, обход DPI перестал работать",
             "Роскомсвобода", summary="VLESS-подключения рвутся, нужен новый конфиг"),
        make("NVIDIA представила видеокарту с ИИ-ускорением", "3DNews Hardware"),
    ]
    candidates = filter_and_dedupe(articles, posted)
    assert len(candidates) == 2
    ranked = select_candidates(candidates, posted)
    assert ranked[0].article.source == "Роскомсвобода"
    assert len(ranked) == 2


def test_duplicates_inside_batch_dropped(db_file):
    posted = PostedManager(db_file)
    a = make("РКН внес Cloudflare в реестр блокировок", "Роскомсвобода",
             link="https://a.example/1")
    b = make("РКН внес Cloudflare в реестр блокировок", "3DNews Software",
             link="https://b.example/2")
    candidates = filter_and_dedupe([a, b], posted)
    assert len(candidates) == 1


def test_filtered_out_articles_get_reason_in_db(db_file):
    posted = PostedManager(db_file)
    bad = make("Открыта вакансия менеджера по продажам", "Habr AI",
               link="https://hh.example/1")
    filter_and_dedupe([bad], posted)
    found, reason = posted.get_rejection_reason(bad.link)
    assert found and "JUNK_VACANCY" in reason
