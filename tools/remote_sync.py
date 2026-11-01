#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Двусторонняя синхронизация posted_articles.db: сервер ↔ репозиторий.

Порядок важен: качаем НЕ живой файл, а атомарный снапшот (sqlite backup API),
иначе рваное чтение работающей WAL-базы даёт «database disk image is malformed».
Загружаем обратно снапшот, при остановленном сервисе и со сносом -wal/-shm.
"""
import datetime
import os
import sys

import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_db import merge, quick_check  # noqa: E402

DB_NAME = "posted_articles.db"

# снапшот средствами самого sqlite: корректная копия живой WAL-базы
SNAPSHOT_CODE = (
    "import sqlite3,sys;"
    "src=sqlite3.connect(sys.argv[1]);"
    "dst=sqlite3.connect(sys.argv[2]);"
    "src.backup(dst);dst.close();src.close();"
    "c=sqlite3.connect(sys.argv[2]);"
    "print('snapshot_check:',c.execute('PRAGMA quick_check').fetchone()[0])"
)

# проверка базы на сервере после заливки
SERVER_VERIFY = (
    "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);"
    "print('сервер integrity:',c.execute('PRAGMA integrity_check').fetchone()[0]);"
    "print('на сервере: posted_articles=%d, post_stats=%d, editorial_queue=%d' % ("
    "c.execute('SELECT COUNT(*) FROM posted_articles').fetchone()[0],"
    "c.execute('SELECT COUNT(*) FROM post_stats').fetchone()[0],"
    "c.execute('SELECT COUNT(*) FROM editorial_queue').fetchone()[0]))"
)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def counts(path: str) -> str:
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        parts = []
        for table in ("posted_articles", "post_stats", "editorial_queue"):
            try:
                parts.append(f"{table}={conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
            except sqlite3.Error:
                parts.append(f"{table}=?")
        return ", ".join(parts)
    finally:
        conn.close()


def run(client, cmd: str, timeout: int = 120) -> tuple:
    """Выполнить команду и ДОЖДАТЬСЯ её завершения (иначе гонка при снятии снапшота)."""
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        print(out)
    if err:
        print(f"[stderr] {err}", file=sys.stderr)
    return code, out


def checkpoint(path: str) -> None:
    """Слить WAL в основной файл: иначе в git/на сервер уедет неполная база."""
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    host, user = env("SSH_HOST"), env("SSH_USER", "root")
    password, target = env("SSH_PASSWORD"), env("TARGET_DIR", "/opt/post-bot")
    repo_db = os.path.abspath(env("REPO_DB", DB_NAME))
    if not (host and user and password):
        print("не хватает DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD")
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, username=user, password=password, timeout=30,
                   banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    live = f"{target}/{DB_NAME}"
    snap = f"{target}/.snapshot-{stamp}.db"
    incoming = os.path.join(os.environ.get("RUNNER_TEMP") or "/tmp", "incoming-posted_articles.db")
    part = incoming + ".part"
    try:
        sftp = client.open_sftp()
        # 1. страховочная копия живого файла + атомарный снапшот средствами sqlite
        run(client, f"mkdir -p {target}/backups && cp {live} {target}/backups/{DB_NAME}.{stamp}")
        code, out = run(client, f'python3 -c "{SNAPSHOT_CODE}" {live} {snap}')
        if code != 0 or "snapshot_check: ok" not in out:
            print("снапшот на сервере не снялся или битый — синхронизация остановлена")
            return 1

        # 2. качаем снапшот (не живой файл) через .part, чтобы не осталось обрезка
        if os.path.exists(part):
            os.remove(part)
        sftp.get(snap, part)
        run(client, f"rm -f {snap}")
        os.replace(part, incoming)
        check = quick_check(incoming)
        print(f"с сервера скачано: {counts(incoming)} (quick_check={check})")
        if check != "ok":
            print("скачанный снапшот повреждён — синхронизация остановлена")
            return 1

        if not os.path.exists(repo_db):
            print(f"в репозитории нет {repo_db}")
            return 1

        # 3. слияние в обе стороны: репозиторий получает данные сервера, снапшот
        #    сервера — данные репозитория. Поэтому post_stats / editorial_queue /
        #    settings, которых нет в репозитории, больше не затираются заливкой.
        report = merge(repo_db, incoming)
        for name, (added, updated) in report.items():
            print(f"слияние {name}: +{added} новых, ~{updated} обновлено")
        checkpoint(repo_db)
        print(f"в репозитории стало: {counts(repo_db)}")

        merge(incoming, repo_db)
        checkpoint(incoming)

        # 4. заливка только на остановленном сервисе, со сносом старого WAL
        upl = f"{target}/.sync-upload.tmp"
        sftp.put(incoming, upl)
        run(client, "systemctl stop post-bot-admin; sleep 2; systemctl is-active post-bot-admin || true")
        run(client, f"rm -f {live}-wal {live}-shm")
        sftp.posix_rename(upl, live)
        sftp.close()
        run(client, "systemctl start post-bot-admin && sleep 5 && systemctl is-active post-bot-admin")
        run(client, f'python3 -c "{SERVER_VERIFY}" {live}')
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
