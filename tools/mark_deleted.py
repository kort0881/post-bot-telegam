#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пометить удалённые из канала посты: гейт не должен считать их «вышедшими».

Зачем. 23.11.2026 из канала удалили два политических поста (7079, 7081), но строки
в posted_articles остались. Гейт считал 5/5 и до конца суток не пускал новые посты.

Как работает: mark(ids) на живой базе сервера ставит deleted_at = UTC now.
Колонка аддитивная, создаётся сама при первом вызове.

Запуск (нужны DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD, TARGET_DIR):
    python tools/mark_deleted.py 7079 7081
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_sync import build_transport  # noqa: E402

DB_NAME = "posted_articles.db"


def mark(ids: list[int], transport) -> int:
    """Пометить посты по message_id. Возвращает число затронутых строк."""
    if not ids:
        return 0
    live = f"{getattr(transport, 'target_dir', '/opt/post-bot')}/{DB_NAME}"
    ids_sql = ",".join(str(int(i)) for i in ids)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    alter = "ALTER TABLE posted_articles ADD COLUMN deleted_at TEXT"
    script = (
        "import sqlite3,sys;"
        "c=sqlite3.connect(sys.argv[1]);"
        "cols=[r[1] for r in c.execute('PRAGMA table_info(posted_articles)')];"
        f"c.execute('''{alter}''') if 'deleted_at' not in cols else None;"
        f"n=c.execute('update posted_articles set deleted_at=? where message_id in ({ids_sql})', (sys.argv[2],)).rowcount;"
        "c.commit();print('marked:',n)"
    )
    code, out = transport.run(f"python3 -c \"{script}\" {live} '{stamp}'")
    if code != 0:
        raise RuntimeError(f"не удалось пометить удалённые посты (код {code})")
    try:
        return int(out.rsplit("marked:", 1)[1].strip())
    except (IndexError, ValueError):
        return 0


def main(argv: list[str]) -> int:
    ids = []
    for part in argv[1:]:
        ids.extend(int(x) for x in part.replace(",", " ").split() if x.strip().isdigit())
    if not ids:
        print("usage: mark_deleted.py <message_id> [message_id ...]")
        return 2
    transport = build_transport()
    try:
        count = mark(ids, transport)
        print(f"✅ помечено удалённых постов: {count} (ids: {ids})")
        return 0
    finally:
        transport.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
