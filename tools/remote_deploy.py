#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выкладка админ-бота на сервер по SSH (пароль из окружения, в логи не пишется)."""
import os
import sys

import paramiko


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def run(client, command: str, timeout: int = 600) -> tuple:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    print(f"$ {command.splitlines()[0][:90]}\n{out.strip()[-1500:]}")
    if err.strip():
        print(f"stderr: {err.strip()[-800:]}")
    return code, out


def main() -> int:
    host, user = env("SSH_HOST"), env("SSH_USER", "root")
    password = env("SSH_PASSWORD")
    target = env("TARGET_DIR", "/opt/post-bot")
    repo = env("REPO_URL")
    branch = env("REPO_BRANCH", "main")
    if not (host and user and password and repo):
        print("не хватает DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASSWORD / REPO_URL")
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=host, username=user, password=password, timeout=30,
                   banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)

    try:
        run(client, "command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git)")
        # venv без ensurepip получается пустым (нет pip внутри) — ставим пакеты системы
        run(client, "export DEBIAN_FRONTEND=noninteractive; "
                    "python3 -c 'import ensurepip' 2>/dev/null || "
                    "(apt-get update -qq && apt-get install -y -qq python3-pip python3-venv 2>&1 | tail -2)")
        # битое окружение от прошлой попытки не переиспользуем
        run(client, f"test -x {target}/.venv/bin/pip || rm -rf {target}/.venv")

        code, _ = run(client, f"test -d {target}/.git && echo yes || echo no")
        if "yes" in _:
            run(client, f"cd {target} && git fetch -q origin {branch} && "
                        f"git checkout -q {branch} && git reset --hard -q origin/{branch}")
        else:
            run(client, f"mkdir -p {target} && git clone -q -b {branch} {repo} {target}")

        env_body = "\n".join([
            f"TELEGRAM_BOT_TOKEN={env('TELEGRAM_BOT_TOKEN')}",
            f"CHANNEL_ID={env('CHANNEL_ID')}",
            f"CHANNEL_USERNAME={env('CHANNEL_USERNAME')}",
            f"ADMIN_IDS={env('ADMIN_IDS')}",
            f"ADMIN_CHAT_ID={env('ADMIN_CHAT_ID') or env('ADMIN_IDS')}",
            f"DB_FILE={target}/posted_articles.db",
            "POSTING_MODE=auto",
            "",
        ])
        sftp = client.open_sftp()
        with sftp.file(f"{target}/.env", "w") as handle:
            handle.write(env_body)
        sftp.chmod(f"{target}/.env", 0o600)
        sftp.close()
        print(f".env записан на сервере ({len(env_body.splitlines()) - 1} переменных)")

        run(client, f"cd {target} && chmod +x deploy/install_adminbot.sh && "
                    f"REPO_URL={repo} bash deploy/install_adminbot.sh {target}", timeout=900)
        code, _ = run(client, "systemctl is-active post-bot-admin")
        run(client, "systemctl --no-pager status post-bot-admin | head -12")
        return 0 if "active" in _ else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
