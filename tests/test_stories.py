"""Сюжетный слой дублей: правила темы и лексическая близость.

Тесты написаны на реальных заголовках канала 12–19.11.2026.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from bot.db import PostedManager
from bot.models import Article
from bot.stories import build_df, find_story, normalise_rows, story_rule, story_tokens
from bot.textutils import normalize_url

RKN_AI = "Роскомнадзор подключил ИИ к системе блокировки VPN и контента в рунете"
RKN_BUDGET = "Роскомнадзор вооружился ИИ за 2,27 млрд рублей — он будет искать VPN и запрещённый контент"
RKN_TRAFFIC = ("Роскомнадзор запустил систему фильтрации трафика с применением ИИ. "
               "На нее потратили два миллиарда рублей - Meduza")
RKN_TECHORA = "Роскомнадзор блокирует частные VPN-серверы для двух человек - Techora.ru"
RKN_HISTORY = "Мы собрали хронику блокировок в России с 2016 года: что показали даты - Хабр"

ANTHROPIC_FIRST = "Anthropic представила Fable 5.2 и Mythos 5.2: что нового"
ANTHROPIC_SECOND = ("ИИ научился писать целые операционные системы: Anthropic представила "
                    "модели Fable 5.2 и Mythos 5.2")

UNRELATED = "OpenAI показала модель GPT-6 и снизила цены на API"


def _make(title, link, summary=""):
    return Article(
        title=title,
        summary=summary,
        link=link,
        source="Google News (Блокировки)",
        published=datetime.now(timezone.utc) - timedelta(hours=1),
    )


def _add(posted: PostedManager, title: str, link: str, age_hours: float = 1):
    """Положить пост в базу с нужным возрастом: add() всегда пишет now()."""
    assert posted.add(_make(title, link), "block", "block") is True
    stamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(posted.db_file)
    conn.execute("UPDATE posted_articles SET posted_date = ? WHERE norm_url = ?",
                 (stamp, normalize_url(link)))
    conn.commit()
    conn.close()


def test_tokens_stem_and_drop_rss_artifacts():
    tokens = story_tokens("Роскомнадзор запустил ИИ&nbsp;— блокировки VPN")
    assert "роском" in tokens
    assert "блокир" in tokens
    assert "nbsp" not in tokens
    assert "запуст" in tokens


def test_rule_matches_rkn_blocking_titles():
    for title in (RKN_AI, RKN_BUDGET, RKN_TRAFFIC, RKN_TECHORA, RKN_HISTORY):
        assert story_rule(title) == "rkn_blocks", title


def test_rule_does_not_match_ai_news():
    assert story_rule(ANTHROPIC_FIRST) is None
    assert story_rule(UNRELATED) is None
    assert story_rule("NVIDIA показала новый GPU") is None


def test_lexical_similarity_finds_reworded_repeat():
    docs, titles = normalise_rows([(ANTHROPIC_FIRST, "")])
    df = build_df(docs)
    match = find_story(story_tokens(ANTHROPIC_SECOND), docs, titles, df, len(docs))
    assert match is not None
    assert "anthro" in match["rare"] or "mythos" in match["rare"]


def test_lexical_similarity_does_not_hit_unrelated():
    docs, titles = normalise_rows([(RKN_AI, ""), (ANTHROPIC_FIRST, "")])
    df = build_df(docs)
    assert find_story(story_tokens(UNRELATED), docs, titles, df, len(docs)) is None


def test_db_blocks_series_about_rkn_by_rule(db_file):
    posted = PostedManager(db_file)
    _add(posted, RKN_BUDGET, "https://example.com/rkn-budget")

    ok, reason = posted.check_story_limit(RKN_AI)
    assert ok is False
    assert reason.startswith("STORY_RULE")


def test_db_allows_ai_news_after_rkn_post(db_file):
    posted = PostedManager(db_file)
    _add(posted, RKN_AI, "https://example.com/rkn-ai")

    ok, reason = posted.check_story_limit(ANTHROPIC_FIRST)
    assert ok is True, reason


def test_db_blocks_reworded_repeat_by_similarity(db_file):
    posted = PostedManager(db_file)
    _add(posted, ANTHROPIC_FIRST, "https://example.com/fable")

    ok, reason = posted.check_story_limit(ANTHROPIC_SECOND)
    assert ok is False
    assert reason.startswith("STORY_DUP")


def test_db_rule_window_expires(db_file):
    """Через 72 ч правило темы отпускает; лексика ещё может поймать пересказ."""
    posted = PostedManager(db_file)
    _add(posted, RKN_BUDGET, "https://example.com/rkn-old", age_hours=100)

    ok, reason = posted.check_story_limit(RKN_AI)
    assert not reason.startswith("STORY_RULE"), reason


def test_db_old_post_does_not_block_fully(db_file):
    """Старше окна 168 ч пост не мешает публикации."""
    posted = PostedManager(db_file)
    _add(posted, RKN_BUDGET, "https://example.com/rkn-ancient", age_hours=200)

    ok, reason = posted.check_story_limit(RKN_AI)
    assert ok is True, reason


def test_db_allows_unrelated_news(db_file):
    posted = PostedManager(db_file)
    _add(posted, RKN_AI, "https://example.com/rkn-ai")

    ok, reason = posted.check_story_limit(UNRELATED, "")
    assert ok is True, reason
