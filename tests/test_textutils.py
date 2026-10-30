
from bot.config import POST_LENGTH_LIMITS
from bot.textutils import (
    TermMatcher,
    get_content_hash,
    is_valid_post_text,
    normalize_title,
    normalize_url,
)


def test_normalize_url_strips_tracking():
    assert normalize_url("https://www.Example.com/a/?utm_source=tg&utm_medium=x") == "example.com/a"
    assert normalize_url("http://example.com/b?page=2") == "example.com/b?page=2"


def test_term_matcher_word_boundaries():
    # точная словоформа: «работа» != «работает» и != «работу»
    strict = TermMatcher(["работа"], stem=False)
    assert strict.any("работает новый протокол") is False
    assert strict.any("ищем на работу") is False
    assert strict.any("работа найдена") is True

    # основы слов по умолчанию: «тест» ловит «тестируем», «ваканси» — «вакансию»
    assert TermMatcher(["тест"]).any("тестируем протокол") is True
    assert TermMatcher(["ваканси"]).any("вакансию закрыли") is True


def test_short_terms_require_whole_word():
    matcher = TermMatcher(["ai", "ии"])
    assert matcher.any("aim high") is False
    assert matcher.any("новый ai-агент") is True


def test_normalize_title_is_stable():
    assert normalize_title("GPT-5 вышел!") == normalize_title("gpt 5 вышел")


def test_content_hash_ignores_spaces():
    assert get_content_hash("текст  с пробелами") == get_content_hash("Текст с пробелами")


def test_short_news_within_format_bounds_is_valid():
    """ТЗ §7: короткая новость 350–600 символов проходит валидацию."""
    body = ("Роскомнадзор расширил реестр блокировок: под ограничения попали "
            "несколько подсетей Cloudflare. Провайдеры начали фильтрацию трафика "
            "на уровне DPI. Пользователи VLESS-конфигураций сообщают о разрывах "
            "соединения. Проверить доступность можно через тест на утечку IP. "
            "Если соединение не восстанавливается, помогает смена SNI и порта, "
            "а также проверка маршрутизации на уровне операционной системы "
            "и повторное тестирование в другой сети.")
    min_len, max_len = POST_LENGTH_LIMITS["news"]
    assert min_len <= len(body) <= max_len, len(body)
    ok, reason = is_valid_post_text(body, min_len, max_len)
    assert ok, reason


def test_too_short_and_too_long_are_rejected():
    ok, reason = is_valid_post_text("Короткий текст. Ещё фраза.", 350)
    assert not ok and reason.startswith("TEXT_TOO_SHORT")
    long_text = "Предложение про блокировки. " * 200
    ok, reason = is_valid_post_text(long_text, 350, 600)
    assert not ok and reason.startswith("TEXT_TOO_LONG")
