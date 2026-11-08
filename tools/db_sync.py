#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""posted_articles.db: единственный писатель — сервер, git — только бэкап.

Почему так. Раньше бинарник posted_articles.db коммитился в git тремя
воркфлоу (постер, метрики, синк с сервера) и был общим изменяемым файлом.
Два параллельных запуска не могут его смержить — git не мержит бинарники:
`git pull --rebase` падает на конфликте, запись о только что опубликованном
посте не уезжает в репозиторий, и следующий тик выбирает тот же материал
заново. Реальный случай: 08.11.2026 15:07 UTC, ран 42493150642 — пост
«Подписчики ChatGPT Pro за $200...» ушёл дважды (message_id 6881 и 6887).

Теперь в git БД едет только на чтение, а пишет всё живое на сервере:

    pull    — снять снапшот живой базы (sqlite backup API на сервере) и слить
              его в локальную копию; дальше работаем с локальной копией;
    push    — снять снапшот ещё раз, слить в обе стороны, залить на сервер при
              остановленном сервисе post-bot-admin, снести -wal/-shm, поднять
              сервис и проверить PRAGMA integrity_check;
    backup  — только чтение сервера: снапшот → слияние в файл репозитория,
              один раз в сутки коммитится в git как бэкап.

Из git на сервер БД не заливается никогда, поэтому «потерянная» в репозитории
запись не может ни воскресить дубль, ни перезаписать живые метрики.

Транспорт: SSH (secrets DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD,
TARGET_DIR=/opt/post-bot) либо локальная папка DB_SYNC_LOCAL_DIR — второй
вариант существует для тестов, ssh в тестах не нужен.

Запуск:
    python tools/db_sync.py pull|push|backup
"""

from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_db import merge, quick_check  # noqa: E402

DB_NAME = "posted_articles.db"
TABLES = ("posted_articles", "post_stats", "editorial_queue")


def checkpoint(path: str) -> None:
    """Слить WAL в основной файл: иначе в снапшот/на сервер уедет неполный образ."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def counts(path: str) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        parts = []
        for table in TABLES:
            try:
                parts.append(f"{table}={conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
            except sqlite3.Error:
                parts.append(f"{table}=?")
        return ", ".join(parts)
    finally:
        conn.close()


class LocalTransport:
    """«Сервер» — локальная папка. Тесты и ручные прогоны без ssh."""

    def __init__(self, root: str, service: str = ""):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.live = self.root / DB_NAME
        self.service = service

    def fetch_snapshot(self, dest: str) -> None:
        if not self.live.exists():
            raise FileNotFoundError(f"на сервере нет базы: {self.live}")
        source = sqlite3.connect(f"file:{self.live}?mode=ro", uri=True)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        state = quick_check(dest)
        if state != "ok":
            raise RuntimeError(f"снапшот с сервера повреждён (quick_check={state})")

    def upload(self, src: str) -> dict:
        for suffix in ("-wal", "-shm"):
            Path(str(self.live) + suffix).unlink(missing_ok=True)
        shutil.copy2(src, self.live)
        state = quick_check(str(self.live))
        if state != "ok":
            raise RuntimeError(f"база на сервере не прошла quick_check={state}")
        return {"integrity": state, "remote_counts": counts(str(self.live))}

    def close(self) -> None:
        return None


class SshTransport:
    """Сервер по ssh: снапшот через sqlite backup API, заливка на остановленном сервисе."""

    SNAPSHOT_CODE = (
        "import sqlite3,sys;"
        "src=sqlite3.connect(sys.argv[1]);"
        "dst=sqlite3.connect(sys.argv[2]);"
        "src.backup(dst);dst.close();src.close();"
        "c=sqlite3.connect(sys.argv[2]);"
        "print('snapshot_check:',c.execute('PRAGMA quick_check').fetchone()[0])"
    )
    VERIFY_CODE = (
        "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);"
        "print('integrity:',c.execute('PRAGMA integrity_check').fetchone()[0]);"
        "print('rows:',c.execute('SELECT COUNT(*) FROM posted_articles').fetchone()[0])"
    )

    def __init__(self, host: str, user: str, password: str, target_dir: str, service: str = "post-bot-admin"):
        import paramiko  # тяжёлый импорт только для настоящего транспорта

        self._paramiko = paramiko
        self.host, self.user, self.password = host, user, password
        self.target_dir = target_dir.rstrip("/")
        self.service = service
        self.live = f"{self.target_dir}/{DB_NAME}"
        self._client = None

    @property
    def client(self):
        if self._client is None:
            client = self._paramiko.SSHClient()
            client.set_missing_host_key_policy(self._paramiko.AutoAddPolicy())
            client.connect(
                self.host,
                username=self.user,
                password=self.password,
                timeout=30,
                banner_timeout=60,
                auth_timeout=60,
                allow_agent=False,
                look_for_keys=False,
            )
            self._client = client
        return self._client

    def run(self, cmd: str, timeout: int = 120) -> tuple:
        _, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        if out:
            print(out)
        if err:
            print(f"[stderr] {err}", file=sys.stderr)
        return code, out

    def fetch_snapshot(self, dest: str) -> None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        snap = f"{self.target_dir}/.snapshot-{stamp}.db"
        self.run(f"mkdir -p {self.target_dir}/backups && cp {self.live} {self.target_dir}/backups/{DB_NAME}.{stamp}")
        code, out = self.run(f'python3 -c "{self.SNAPSHOT_CODE}" {self.live} {snap}')
        if code != 0 or "snapshot_check: ok" not in out:
            raise RuntimeError("снапшот на сервере не снялся или битый — синхронизация остановлена")
        part = dest + ".part"
        try:
            sftp = self.client.open_sftp()
            sftp.get(snap, part)
            sftp.close()
        finally:
            self.run(f"rm -f {snap}")
        os.replace(part, dest)

    def upload(self, src: str) -> dict:
        sftp = self.client.open_sftp()
        upl = f"{self.target_dir}/.sync-upload.tmp"
        try:
            sftp.put(src, upl)
            # заливка только на остановленном сервисе, иначе он держит старый файл
            self.run(f"systemctl stop {self.service}; sleep 2; systemctl is-active {self.service} || true")
            self.run(f"rm -f {self.live}-wal {self.live}-shm")
            sftp.posix_rename(upl, self.live)
        finally:
            sftp.close()
        self.run(f"systemctl start {self.service} && sleep 5 && systemctl is-active {self.service}")
        code, out = self.run(f'python3 -c "{self.VERIFY_CODE}" {self.live}')
        if "integrity: ok" not in out:
            raise RuntimeError(f"после заливки база на сервере не прошла integrity_check:\n{out}")
        return {"integrity": "ok", "remote_counts": out}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def build_transport():
    local_dir = (os.environ.get("DB_SYNC_LOCAL_DIR") or "").strip()
    if local_dir:
        return LocalTransport(local_dir, os.environ.get("REMOTE_SERVICE", ""))
    host = (os.environ.get("SSH_HOST") or "").strip()
    user = (os.environ.get("SSH_USER") or "root").strip()
    password = (os.environ.get("SSH_PASSWORD") or "").strip()
    target = (os.environ.get("TARGET_DIR") or "/opt/post-bot").strip()
    service = (os.environ.get("REMOTE_SERVICE") or "post-bot-admin").strip()
    if not (host and user and password):
        raise RuntimeError("не хватает DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD")
    return SshTransport(host, user, password, target, service)


def _merge_report(report: dict) -> str:
    return "; ".join(f"{name}: +{added} новых, ~{updated} обновлено" for name, (added, updated) in report.items())


def pull(db_path: str, transport, log=print) -> dict:
    """Снапшот сервера → локальная база (union, локальные записи не теряются)."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "snapshot.db")
        transport.fetch_snapshot(snap)
        if not os.path.exists(db_path):
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            shutil.copy2(snap, db_path)
            checkpoint(db_path)
            log(f"📥 локальной базы не было — взяли снапшот сервера: {counts(db_path)}")
            return {"mode": "created", "counts": counts(db_path)}
        report = merge(db_path, snap)
        checkpoint(db_path)
        log(f"📥 сервер → локально: {_merge_report(report)}; итого {counts(db_path)}")
        return {"mode": "merged", "merged": report, "counts": counts(db_path)}


def push(db_path: str, transport, attempts: int = 3, delay: float = 5.0, log=print) -> dict:
    """Локальная база → сервер. Ретраи: потерянная запись = повторная публикация."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                snap = os.path.join(tmp, "snapshot.db")
                transport.fetch_snapshot(snap)
                # в обе стороны: наши посты на сервер, метрики и правки админа к нам
                back = merge(db_path, snap)
                info = merge(snap, db_path)
                checkpoint(snap)
                result = transport.upload(snap)
                log(f"📤 локально → сервер (попытка {attempt}): {_merge_report(back)}; "
                    f"{_merge_report(info)}; на сервере {result.get('remote_counts')}")
                return {"attempts": attempt, "merged_from_server": back, "merged_to_server": info, **result}
        except Exception as exc:  # noqa: BLE001 — транспорт бывает любой, важен ретрай
            last_error = exc
            log(f"⚠️ заливка на сервер не удалась (попытка {attempt}/{attempts}): {exc}")
            if attempt < attempts:
                time.sleep(delay * attempt)
    raise RuntimeError(f"не удалось залить базу на сервер за {attempts} попыток: {last_error}")


def backup(db_path: str, transport, log=print) -> dict:
    """Только чтение сервера: снапшот → файл репозитория (для git-бэкапа)."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "snapshot.db")
        transport.fetch_snapshot(snap)
        report = merge(db_path, snap)
        checkpoint(db_path)
        log(f"🗂 снапшот сервера в бэкап репозитория: {_merge_report(report)}; итого {counts(db_path)}")
        return {"merged": report, "counts": counts(db_path)}


def main(argv: list[str]) -> int:
    actions = {"pull": pull, "push": push, "backup": backup}
    command = argv[1] if len(argv) > 1 else "pull"
    if command not in actions:
        print(f"usage: db_sync.py {'|'.join(actions)}")
        return 2
    db_path = os.path.abspath(os.environ.get("REPO_DB") or DB_NAME)
    transport = build_transport()
    try:
        report = actions[command](db_path, transport)
        print(f"✅ {command}: {report}")
        return 0
    finally:
        transport.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
