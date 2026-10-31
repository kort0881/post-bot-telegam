#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка развёрнутого админ-бота: сервис, лог, getMe с сервера."""
import os
import sys

import paramiko


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def main() -> int:
    host, user = env("SSH_HOST"), env("SSH_USER", "root")
    password, target = env("SSH_PASSWORD"), env("TARGET_DIR", "/opt/post-bot")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, username=user, password=password, timeout=30,
                   banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)
    try:
        for command in (
            "systemctl is-active post-bot-admin",
            "systemctl is-enabled post-bot-admin",
            f"tail -n 15 /var/log/post-bot-admin.log 2>/dev/null || journalctl -u post-bot-admin -n 15 --no-pager",
            f"cd {target} && set -a && . ./.env && set +a && ./.venv/bin/python tools/verify_bot.py",
        ):
            stdin, stdout, stderr = client.exec_command(command, timeout=120)
            out = stdout.read().decode(errors="replace").strip()
            err = stderr.read().decode(errors="replace").strip()
            print(f"$ {command[:80]}\n{out}\n{('stderr: ' + err[:300]) if err else ''}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
