#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Двусторонняя синхронизация posted_articles.db: сервер ↔ репозиторий."""
import datetime
import os
import sys

import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_db import merge  # noqa: E402

DB_NAME = "posted_articles.db"


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
    incoming = "/tmp/incoming-posted_articles.db"
    try:
        sftp = client.open_sftp()
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        client.exec_command(f"mkdir -p {target}/backups && "
                            f"cp {target}/{DB_NAME} {target}/backups/{DB_NAME}.{stamp}")
        sftp.get(f"{target}/{DB_NAME}", incoming)
        print(f"с сервера скачано: {counts(incoming)}")

        if not os.path.exists(repo_db):
            print(f"в репозитории нет {repo_db}")
            return 1
        report = merge(repo_db, incoming)
        for name, (added, updated) in report.items():
            print(f"слияние {name}: +{added} новых, ~{updated} обновлено")
        print(f"в репозитории стало: {counts(repo_db)}")

        sftp.put(repo_db, f"{target}/.sync-upload.tmp")
        sftp.posix_rename(f"{target}/.sync-upload.tmp", f"{target}/{DB_NAME}")
        sftp.close()
        stdin, stdout, _ = client.exec_command("systemctl restart post-bot-admin && sleep 4 && "
                                               "systemctl is-active post-bot-admin")
        print(f"сервис после синхронизации: {stdout.read().decode().strip()}")
        stdin, stdout, _ = client.exec_command(f"cd {target} && python3 -c \"import sqlite3;"
                                               "c=sqlite3.connect('posted_articles.db');"
                                               "print('на сервере: posted_articles=%d, post_stats=%d, editorial_queue=%d' % ("
                                               "c.execute('SELECT COUNT(*) FROM posted_articles').fetchone()[0],"
                                               "c.execute('SELECT COUNT(*) FROM post_stats').fetchone()[0],"
                                               "c.execute('SELECT COUNT(*) FROM editorial_queue').fetchone()[0]))\"")
        print(stdout.read().decode().strip())
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
