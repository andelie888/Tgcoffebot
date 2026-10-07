#!/usr/bin/env bash
# Развёртывание Coffee Manager на сервер (Ubuntu 24.04).
# Первый запуск:      bash deploy.sh <IP>
# Обновление кода:    bash deploy.sh <IP>   (база и .env на сервере НЕ перезаписываются)
set -euo pipefail

IP="${1:?Укажи IP сервера: bash deploy.sh 1.2.3.4}"
cd "$(dirname "$0")"
KEY=".deploy/vultr_key"
APP="/opt/coffee-manager"
chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new root@$IP"

echo "1/6 Проверяю связь с сервером…"
$SSH "echo ok" >/dev/null

echo "2/6 Ставлю Python на сервер (1–2 минуты при первом запуске)…"
$SSH "export DEBIAN_FRONTEND=noninteractive; command -v rsync >/dev/null && dpkg -s python3-venv >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq python3-venv python3-pip rsync sqlite3 >/dev/null); mkdir -p $APP/backups"

FIRST=0
$SSH "test -f $APP/coffee_manager.db" || FIRST=1

if [ "$FIRST" = 1 ]; then
  echo "3/6 Первый запуск: останавливаю бота на Mac, чтобы не было двух ботов…"
  pkill -f "python.*bot.py" 2>/dev/null || true
  sleep 2
fi

echo "4/6 Копирую код…"
rsync -az -e "ssh -i $KEY" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.deploy' \
  --exclude 'backups' --exclude '*.log' --exclude '*.db' --exclude '.env' --exclude 'shop_data.json' \
  ./ "root@$IP:$APP/"

if [ "$FIRST" = 1 ]; then
  echo "    Первый запуск: копирую .env и базу…"
  rsync -az -e "ssh -i $KEY" .env coffee_manager.db "root@$IP:$APP/"
fi

echo "5/6 Ставлю зависимости и автозапуск…"
$SSH "cd $APP && (test -d .venv || python3 -m venv .venv) && .venv/bin/pip install -q -r requirements.txt && cat > /etc/systemd/system/coffee-manager.service <<UNIT
[Unit]
Description=Coffee Manager Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python $APP/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable -q coffee-manager && systemctl restart coffee-manager"

echo "6/6 Проверяю…"
sleep 5
$SSH "systemctl is-active coffee-manager && journalctl -u coffee-manager -n 5 --no-pager"
echo
echo "Готово: бот работает на сервере $IP. Mac можно выключать."
