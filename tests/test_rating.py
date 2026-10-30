from datetime import datetime, timedelta, timezone

from bot.models import Article
from bot.rating import diversify, priority_band, rank_candidates, score_article


def make(title, source, age_hours=1, summary=""):
    return Article(
        title=title,
        summary=summary,
        link=f"https://{source.lower().replace(' ', '')}.example/{abs(hash(title))}",
        source=source,
        published=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


def test_important_block_news_from_secondary_source_wins():
    """ТЗ §14.8: важная новость о блокировках не проигрывает второстепенной из 3DNews."""
    important = make(
        "Роскомнадзор заблокировал Cloudflare: что делать пользователям VLESS",
        source="Роскомсвобода",
        age_hours=2,
        summary="Обход блокировок через DPI перестал работать, нужен новый VLESS-конфиг",
    )
    minor = make(
        "NVIDIA представила новую версию драйвера для видеокарт",
        source="3DNews Hardware",
        age_hours=2,
    )
    ranked = rank_candidates([minor, important])
    assert ranked[0].article is important
    assert ranked[0].score.total > ranked[1].score.total


def test_score_bounds_and_bands():
    article = make("Блокировка DPI: как проверить и что делать", "Роскомсвобода")
    breakdown = score_article(article)
    data = breakdown.as_dict()
    assert 0 <= data["total"] <= 100
    assert data["topic"] <= 25 and data["usefulness"] <= 25
    assert data["freshness"] <= 20 and data["novelty"] <= 15
    assert data["discussion"] <= 10 and data["reliability"] <= 5

    assert priority_band(85) == "priority"
    assert priority_band(70) == "publish"
    assert priority_band(55) == "queue"
    assert priority_band(20) == "reject"


def test_freshness_decays():
    fresh = score_article(make("Обход блокировок: сбой VLESS", "Роскомсвобода", age_hours=1))
    old = score_article(make("Обход блокировок: сбой VLESS", "Роскомсвобода", age_hours=200))
    assert fresh.freshness > old.freshness


def test_diversify_keeps_rank_but_caps_repeats():
    items = [make(f"Блокировка DPI новость {i}", "3DNews Software") for i in range(5)]
    ranked = rank_candidates(items)
    limited = diversify(ranked, max_per_source=2, max_per_topic=2)
    assert len(limited) == len(ranked)  # ничего не теряется
    assert len({id(c) for c in limited}) == len(ranked)
    assert limited[0].article is ranked[0].article  # рейтинг не перевернулся
