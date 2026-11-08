# -*- coding: utf-8 -*-
"""Тесты синхронизации базы: сервер — единственный писатель, git — только бэкап.

Проверяем ровно тот сценарий, из-за которого 08.11.2026 пост ушёл в канал
дважды: запись об опубликованном материале должна доезжать до сервера при
любом раскладе, а слияние не должно плодить дубли по norm_url.
"""

import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from db_sync import DB_NAME, LocalTransport, backup, pull, push  # noqa: E402

SCHEMA = """
CREATE TABLE posted_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    norm_url TEXT UNIQUE,
    title TEXT,
    posted_date TEXT
);
CREATE TABLE post_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    views_1h INTEGER,
    views_24h INTEGER
);
"""


def _make_db(path, articles=(), stats=()):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO posted_articles (url, norm_url, title, posted_date) VALUES (?, ?, ?, ?)",
            list(articles),
        )
        conn.executemany(
            "INSERT INTO post_stats (message_id, views_1h) VALUES (?, ?)",
            list(stats),
        )
        conn.commit()
    finally:
        conn.close()


def _add_post(path, norm_url, title, message_id):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO posted_articles (url, norm_url, title, posted_date) VALUES (?, ?, ?, ?)",
            (norm_url, norm_url, title, "2026-11-08 17:09:32"),
        )
        conn.execute("INSERT INTO post_stats (message_id, views_1h) VALUES (?, 0)", (message_id,))
        conn.commit()
    finally:
        conn.close()


def _urls(path):
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT norm_url FROM posted_articles ORDER BY norm_url")]
    finally:
        conn.close()


def _views(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT message_id, views_1h FROM post_stats"))
    finally:
        conn.close()


def test_pull_merges_server_state_into_local(tmp_path):
    """pull не теряет ни локальные, ни серверные записи."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")],
             stats=[(6870, 15)])
    local = tmp_path / DB_NAME
    _make_db(local, articles=[("u2", "https://e.com/2", "статья 2", "2026-11-08 12:00:00")])

    report = pull(str(local), LocalTransport(str(server)), log=lambda *_: None)

    assert report["counts"].startswith("posted_articles=2")
    assert _urls(local) == ["https://e.com/1", "https://e.com/2"]
    assert _views(local) == {6870: 15}


def test_pull_creates_local_db_when_missing(tmp_path):
    """На чистом раннере локальной базы может не быть — берём снапшот целиком."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")])
    local = tmp_path / "nested" / DB_NAME

    report = pull(str(local), LocalTransport(str(server)), log=lambda *_: None)

    assert report["mode"] == "created"
    assert _urls(local) == ["https://e.com/1"]


def test_push_delivers_published_post_to_server(tmp_path):
    """Запись об опубликованном посте уезжает на сервер — иначе будет дубль."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")],
             stats=[(6870, 15)])
    local = tmp_path / DB_NAME
    _make_db(local,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")],
             stats=[(6870, 15)])
    _add_post(local, "https://e.com/chatgpt-pro", "ChatGPT Pro", 6887)

    report = push(str(local), LocalTransport(str(server)), attempts=2, delay=0.0, log=lambda *_: None)

    assert report["integrity"] == "ok"
    assert "https://e.com/chatgpt-pro" in _urls(server / DB_NAME)
    assert _views(server / DB_NAME) == {6870: 15, 6887: 0}


def test_push_keeps_server_metrics_and_does_not_duplicate(tmp_path):
    """Метрики, собранные на сервере, не затираются; norm_url уникален."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")],
             stats=[(6870, 120)])
    local = tmp_path / DB_NAME
    _make_db(local,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")],
             stats=[(6870, 40)])

    push(str(local), LocalTransport(str(server)), attempts=1, delay=0.0, log=lambda *_: None)

    assert _views(server / DB_NAME) == {6870: 120}
    assert _urls(server / DB_NAME) == ["https://e.com/1"]


def test_push_retries_before_giving_up(tmp_path):
    """Первый обрыв транспорта не должен стоить потерянной записи."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")])
    local = tmp_path / DB_NAME
    _make_db(local,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")])
    _add_post(local, "https://e.com/chatgpt-pro", "ChatGPT Pro", 6887)

    transport = LocalTransport(str(server))
    calls = {"n": 0}
    original = transport.upload

    def flaky_upload(src):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("ssh оборвался")
        return original(src)

    transport.upload = flaky_upload
    report = push(str(local), transport, attempts=3, delay=0.0, log=lambda *_: None)

    assert calls["n"] == 2
    assert report["attempts"] == 2
    assert "https://e.com/chatgpt-pro" in _urls(server / DB_NAME)


def test_backup_is_read_only_for_server(tmp_path):
    """backup забирает снапшот в файл репозитория и ничего не пишет на сервер."""
    server = tmp_path / "server"
    server.mkdir()
    _make_db(server / DB_NAME,
             articles=[("u1", "https://e.com/1", "статья 1", "2026-11-08 10:00:00")])
    local = tmp_path / DB_NAME
    _make_db(local, articles=[("u2", "https://e.com/2", "статья 2", "2026-11-08 12:00:00")])

    backup(str(local), LocalTransport(str(server)), log=lambda *_: None)

    assert _urls(local) == ["https://e.com/1", "https://e.com/2"]
    assert _urls(server / DB_NAME) == ["https://e.com/1"]
