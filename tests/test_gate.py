# -*- coding: utf-8 -*-
"""Гейт расписания: удалённые посты не считаются, пауза и окно тишины работают."""
import datetime
import sqlite3

from tools.gate import decide

UTC = datetime.timezone.utc


def make_db(path, rows):
    """rows: [(posted_date, message_id|None, deleted_at|None)]"""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE posted_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL, norm_url TEXT NOT NULL UNIQUE, domain TEXT NOT NULL,
            title TEXT NOT NULL, title_normalized TEXT NOT NULL,
            posted_date TEXT DEFAULT CURRENT_TIMESTAMP,
            message_id INTEGER, deleted_at TEXT
        )"""
    )
    for i, (posted, mid, deleted) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO posted_articles(url,norm_url,domain,title,title_normalized,posted_date,message_id,deleted_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (f"https://e/{i}", f"https://e/{i}", "e", f"t{i}", f"t{i}", posted, mid, deleted),
        )
    conn.commit()
    conn.close()


def test_deleted_posts_not_counted(tmp_path):
    db = tmp_path / "db.sqlite"
    now = datetime.datetime(2026, 11, 27, 13, 30, tzinfo=UTC)
    make_db(db, [
        ("2026-11-27 09:00:00", 7079, "2026-11-27 10:00:00"),   # удалён
        ("2026-11-27 09:10:00", 7080, None),                    # живой
        ("2026-11-27 11:00:00", 7081, "2026-11-27 12:00:00"),   # удалён
    ])
    info = decide(str(db), now)
    assert info["have"] == 1
    assert info["last"] == "2026-11-27 09:10:00"
    assert info["run"] == 1


def test_deleted_all_means_free_slot(tmp_path):
    db = tmp_path / "db.sqlite"
    now = datetime.datetime(2026, 11, 23, 20, 7, tzinfo=UTC)
    make_db(db, [
        ("2026-11-23 16:40:00", 7079, "2026-11-23 17:00:00"),
        ("2026-11-23 17:00:00", 7081, "2026-11-23 17:10:00"),
    ])
    info = decide(str(db), now)
    assert info["have"] == 0 and info["run"] == 1 and info["last"] is None


def test_gap_blocks(tmp_path):
    db = tmp_path / "db.sqlite"
    now = datetime.datetime(2026, 11, 27, 12, 30, tzinfo=UTC)
    make_db(db, [("2026-11-27 11:30:00", 7001, None)])
    info = decide(str(db), now)
    assert info["gap_ok"] is False and info["run"] == 0


def test_target_closed(tmp_path):
    db = tmp_path / "db.sqlite"
    now = datetime.datetime(2026, 11, 27, 19, 7, tzinfo=UTC)
    rows = [(f"2026-11-27 {h:02d}:00:00", 7100 + h, None) for h in (5, 9, 12)]
    make_db(db, rows)
    info = decide(str(db), now)
    assert info["have"] == 3 and info["target"] == 5 and info["run"] == 1


def test_quiet_window_after_reservation(tmp_path):
    db = tmp_path / "db.sqlite"
    make_db(db, [])
    reserved = datetime.datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
    inside = decide(str(db), reserved + datetime.timedelta(minutes=25), reserved_at=reserved)
    after = decide(str(db), reserved + datetime.timedelta(minutes=61), reserved_at=reserved)
    assert inside["quiet"] is True and inside["run"] == 0
    assert after["quiet"] is False and after["run"] == 1


def test_manual_bypasses_everything(tmp_path):
    db = tmp_path / "db.sqlite"
    now = datetime.datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    rows = [(f"2026-11-27 {h:02d}:00:00", 7200 + h, None) for h in (5, 9)]
    make_db(db, rows)
    info = decide(str(db), now, manual=True, reserved_at=now)
    assert info["run"] == 1 and info["manual"] is True
