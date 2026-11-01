#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Слияние двух баз post-bot без потерь: union по ключам, метрики — по максимуму."""
import sqlite3
import sys

# ключ, по которому склеиваем строки
KEYS = {
    "posted_articles": "norm_url",
    "rejected_urls": "norm_url",
    "post_stats": "message_id",
    "editorial_queue": "norm_url",
}

# числовые метрики, для которых при конфликте берём максимум (просмотры только растут)
MAX_COLUMNS = {"views_1h", "views_6h", "views_24h", "forwards_24h", "reactions_24h", "comments_24h"}


def table_columns(conn: sqlite3.Connection, table: str) -> list:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def merge_table(target: sqlite3.Connection, source: sqlite3.Connection, table: str) -> tuple:
    key = KEYS[table]
    tgt_cols = table_columns(target, table)
    src_cols = set(table_columns(source, table))
    # id — автоинкремент: чужой id переносить нельзя, иначе конфликт по первичному ключу
    cols = [c for c in tgt_cols if c in src_cols and c != "id"]
    added = updated = 0
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)

    for row in source.execute(f"SELECT {col_list} FROM {table}").fetchall():
        data = dict(zip(cols, row, strict=True))
        key_value = data[key]
        existing = target.execute(
            f"SELECT {col_list} FROM {table} WHERE {key} = ?", (key_value,)
        ).fetchone()
        if existing is None:
            target.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                           [data[c] for c in cols])
            added += 1
            continue
        current = dict(zip(cols, existing, strict=True))
        changes = {}
        for column, value in data.items():
            if column == key or value is None:
                continue
            if column in MAX_COLUMNS:
                old = current.get(column)
                if old is None or value > old:
                    changes[column] = value
            elif current.get(column) is None and value is not None:
                changes[column] = value
        if table == "editorial_queue" and data.get("status") and current.get("status") == "pending":
            if data["status"] != "pending":
                changes["status"] = data["status"]
        if changes:
            assignments = ", ".join(f"{c} = ?" for c in changes)
            target.execute(f"UPDATE {table} SET {assignments} WHERE {key} = ?",
                           [*changes.values(), key_value])
            updated += 1
    return added, updated


def merge_settings(target: sqlite3.Connection, source: sqlite3.Connection) -> tuple:
    if not has_table(source, "settings"):
        return 0, 0
    if not has_table(target, "settings"):
        # у репозитория таблицы может не быть вовсе — создаём по DDL сервера,
        # иначе настройки теряются при заливке базы обратно
        ddl = source.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        if not ddl or not ddl[0]:
            return 0, 0
        target.execute(ddl[0])
    added = updated = 0
    for key, value in source.execute("SELECT key, value FROM settings").fetchall():
        row = target.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            target.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
            added += 1
        elif row[0] != value:
            target.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
            updated += 1
    return added, updated


def quick_check(conn) -> str:
    """'ok' или текст ошибки. Принимает соединение или путь к файлу базы."""
    if isinstance(conn, sqlite3.Connection):
        return conn.execute("PRAGMA quick_check").fetchone()[0]
    probe = sqlite3.connect(f"file:{conn}?mode=ro", uri=True)
    try:
        return probe.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        probe.close()


def merge(target_path: str, source_path: str) -> dict:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        state = quick_check(source)
        if state != "ok":
            # рваная копия живой базы: сливать нечего, половина строк не читается
            raise sqlite3.DatabaseError(
                f"источник повреждён (quick_check={state}): {source_path}"
            )
    finally:
        source.close()

    target = sqlite3.connect(target_path)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    report = {}
    try:
        target.execute("BEGIN IMMEDIATE")
        for table in KEYS:
            if not (has_table(target, table) and has_table(source, table)):
                continue
            report[table] = merge_table(target, source, table)
        report["settings"] = merge_settings(target, source)
        target.commit()
    except BaseException:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()
    return report


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: merge_db.py <куда> <откуда>")
        raise SystemExit(2)
    for name, (added, updated) in merge(sys.argv[1], sys.argv[2]).items():
        print(f"{name}: +{added} новых, ~{updated} обновлено")
