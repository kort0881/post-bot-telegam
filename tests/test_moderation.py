"""Тесты автомодерации группы: чистые функции + лог в SQLite."""
from bot import moderation


def test_sale_of_keys_is_ban():
    verdict = moderation.classify("Продам VLESS ключи, 200р месяц, пиши в лс")
    assert verdict.action == "delete_ban"
    assert "продам" in verdict.reason


def test_work_from_home_with_link_is_ban():
    verdict = moderation.classify(
        "Заработок от 3000 в день, подробности тут https://example.com/join")
    assert verdict.action == "delete_ban"


def test_invite_to_foreign_chat_is_ban():
    verdict = moderation.classify("t.me/+AbCdEfGhIj заходите, там раздают ключи")
    assert verdict.action == "delete_ban"


def test_normal_question_is_kept():
    verdict = moderation.classify(
        "Перестал подключаться HAPP после обновления, что смотреть первым?")
    assert verdict.action is None
    assert verdict.score == 0


def test_own_domain_link_is_kept():
    verdict = moderation.classify("Вот актуальные ключи https://cdn.dostupnety.website/sub/abc")
    assert verdict.action is None
    assert moderation.extract_links("https://cdn.dostupnety.website/sub/abc") == []


def test_advertising_without_link_is_deleted():
    verdict = moderation.classify("Ищу подработку онлайн без опыта, кто подскажет?")
    assert verdict.action in ("delete", "delete_ban")


def test_plain_link_is_not_spam():
    verdict = moderation.classify("Разбор тут https://habr.com/ru/articles/123456/")
    assert verdict.action is None


def test_flood_counter_detects_repeats(tmp_path):
    store = moderation.ModerationStore(str(tmp_path / "mod.db"))
    text = "Ключи раздают бесплатно"
    counts = [store.seen_before(text, 42) for _ in range(3)]
    assert counts == [0, 1, 2]
    # другой автор — свой счётчик
    assert store.seen_before(text, 43) == 0
    store.close()


def test_log_and_stats(tmp_path):
    store = moderation.ModerationStore(str(tmp_path / "mod.db"))
    store.log(-1002792034242, 10, 42, "spammer", "delete", "нарушение", "Продам ключи")
    entries = store.tail(5)
    assert len(entries) == 1
    assert entries[0]["username"] == "spammer"
    assert "Продам ключи" in moderation.render_log(entries)
    assert store.stats(7) == {"delete": 1}
    assert "решений" in moderation.render_stats(store.stats(7))
    store.close()


def test_empty_log_message():
    assert "пуст" in moderation.render_log([])
