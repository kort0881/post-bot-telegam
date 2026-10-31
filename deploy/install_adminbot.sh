#!/usr/bin/env bash
# Установка админ-бота на сервер (Debian/Ubuntu, root).
# Использование: bash deploy/install_adminbot.sh /opt/post-bot
# Перед запуском положить .env рядом: TELEGRAM_BOT_TOKEN, ADMIN_IDS, ADMIN_CHAT_ID, CHANNEL_ID, DB_FILE.
set -euo pipefail

DIR="${1:-/opt/post-bot}"
REPO="${REPO_URL:-https://github.com/kort0881/post-bot-telegam.git}"

id -u >/dev/null 2>&1 || true
command -v python3 >/dev/null || { echo "нет python3"; exit 1; }

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --quiet origin main
  git -C "$DIR" checkout --quiet main
  git -C "$DIR" pull --quiet --ff-only
else
  mkdir -p "$DIR"
  git clone --quiet "$REPO" "$DIR"
fi

cd "$DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "ОШИБКА: нет $DIR/.env (нужны TELEGRAM_BOT_TOKEN, ADMIN_IDS, ADMIN_CHAT_ID, CHANNEL_ID, DB_FILE)" >&2
  exit 2
fi

install -m 644 deploy/post-bot-admin.service /etc/systemd/system/post-bot-admin.service
systemctl daemon-reload
systemctl enable --now post-bot-admin
sleep 5
systemctl --no-pager --lines=5 status post-bot-admin | head -12
echo "Логи: journalctl -u post-bot-admin -f / tail -f /var/log/post-bot-admin.log"
