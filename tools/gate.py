#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гейт расписания постера: решает, публиковать ли на этом тике.

Почему отдельным модулем. Раньше логика жила heredoc-ом внутри
`.github/workflows/post_bot.yml` и её нельзя было ни протестировать, ни
переиспользовать. Из-за этого 23.11.2026 гейт считал удалённые из канала
посты (2 из 5) и до конца суток не пускал новые.

Правила (слово владельца 26.11.2026 в дополнение к прежним):
- считаем только ЖИВЫЕ посты: строки с deleted_at пустые;
- после любого своего поста минимум MIN_GAP_HOURS тишины;
- бронь (RESERVED_AT_UTC) — окно тишины QUIET_MIN минут после забронированного
  поста, даже если его ставили руками и в базе его нет.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys

SLOTS = [5, 9, 12, 15, 18]      # целевые часы UTC = 8/12/15/18/21 МСК
MAX_POSTS_PER_DAY = 5
MIN_GAP_HOURS = 2
QUIET_MIN_DEFAULT = 60


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _parse_dt(value) -> datetime.datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def day_stats(db_path: str, now: datetime.datetime) -> tuple[int, datetime.datetime | None]:
    """(сколько живых постов за сутки UTC, время последнего живого поста)."""
    if not os.path.exists(db_path):
        return 0, None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = _columns(conn, "posted_articles")
        alive = " and coalesce(deleted_at,'')=''" if "deleted_at" in cols else ""
        today = now.strftime("%Y-%m-%d")
        have = conn.execute(
            f"select count(*) from posted_articles where date(posted_date)=?{alive}", (today,)
        ).fetchone()[0]
        row = conn.execute(
            f"select max(posted_date) from posted_articles where date(posted_date)=?{alive}", (today,)
        ).fetchone()
        return int(have), _parse_dt(row[0] if row else None)
    finally:
        conn.close()


def decide(db_path: str, now_utc: datetime.datetime | None = None, manual: bool = False,
           reserved_at: datetime.datetime | None = None,
           quiet_min: int = QUIET_MIN_DEFAULT) -> dict:
    now = (now_utc or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    due = sum(1 for h in SLOTS if now.hour >= h)
    target = min(due, MAX_POSTS_PER_DAY)
    have, last = day_stats(db_path, now)
    gap_ok = True
    if last is not None:
        gap_ok = (now - last).total_seconds() >= MIN_GAP_HOURS * 3600
    quiet_until = None
    quiet = False
    if reserved_at is not None:
        quiet_until = reserved_at + datetime.timedelta(minutes=int(quiet_min))
        quiet = reserved_at <= now < quiet_until
    run = 1 if manual else int(have < target and gap_ok and not quiet)
    reason = []
    if manual:
        reason.append("ручной запуск")
    if have >= target:
        reason.append(f"цель {target} закрыта ({have})")
    if not gap_ok:
        reason.append(f"пауза {MIN_GAP_HOURS}ч не вышла")
    if quiet:
        reason.append(f"окно тишины после брони до {quiet_until:%H:%M} UTC")
    return {
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "due": due, "target": target, "have": have,
        "last": last.strftime("%Y-%m-%d %H:%M:%S") if last else None,
        "gap_ok": gap_ok, "quiet": quiet,
        "quiet_until": quiet_until.strftime("%Y-%m-%d %H:%M") if quiet_until else None,
        "manual": manual, "run": run,
        "reason": "; ".join(reason) or "можно публиковать",
    }


def _reserved_from_env(now: datetime.datetime) -> datetime.datetime | None:
    raw = (os.environ.get("RESERVED_AT_UTC") or "").strip()
    if not raw:
        return None
    try:
        items = json.loads(raw) if raw.startswith("[") else [raw]
    except json.JSONDecodeError:
        items = [raw]
    times = [t for t in (_parse_dt(x) for x in items) if t is not None]
    if not times:
        return None
    # ближайшая бронь не старше суток назад
    times = [t for t in times if t - datetime.timedelta(days=1) <= now]
    return min(times) if times else None


def main() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    manual = (os.environ.get("EVENT_NAME") == "workflow_dispatch"
              and os.environ.get("INPUT_MODE", "manual") != "tick")
    reserved = _reserved_from_env(now)
    quiet_min = int(os.environ.get("QUIET_MIN") or QUIET_MIN_DEFAULT)
    db_path = os.environ.get("REPO_DB") or "posted_articles.db"
    info = decide(db_path, now, manual=manual, reserved_at=reserved, quiet_min=quiet_min)
    print(f"UTC {info['now']} | цель {info['target']}/{MAX_POSTS_PER_DAY} | постов за сегодня "
          f"{info['have']} | последний {info['last'] or '-'} | пауза {MIN_GAP_HOURS}ч: {info['gap_ok']} "
          f"| ручной {info['manual']} | бронь {reserved.strftime('%Y-%m-%d %H:%M') if reserved else '-'} "
          f"| запускать {info['run']} ({info['reason']})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={info['run']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
