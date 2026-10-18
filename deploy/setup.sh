#!/usr/bin/env bash
# Установка бота unknown shop на чистый Ubuntu/Debian сервер.
# Запуск: bash /opt/unknownshop/deploy/setup.sh
# Повторный запуск безопасен: так же переводит старую установку на отдельного пользователя.
set -euo pipefail
umask 022

APP_DIR=/opt/unknownshop
APP_USER=unknownshop
cd "$APP_DIR"

echo "==> Пакеты"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip

echo "==> Файл подкачки 1 ГБ (страховка для маленькой памяти)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Системный пользователь $APP_USER (бот не работает от root)"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> Python-окружение"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

mkdir -p data

# При повторном запуске бот уже работает: пока ниже меняются права на data/, он не смог бы
# записать оплату в базу. Останавливаем только здесь — если pip выше упадёт, бот продолжит
# работать. В конце скрипт запустит его снова
systemctl stop unknownshop 2>/dev/null || true

echo "==> Права: код принадлежит root, бот пишет только в data/, .env читают root и бот"
chown -R root:root "$APP_DIR"
chmod -R u=rwX,go=rX "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"
chmod 750 "$APP_DIR/data"
if [ -f .env ]; then
  chown "root:$APP_USER" .env
  chmod 640 .env
fi

echo "==> Автозапуск (systemd)"
cat > /etc/systemd/system/unknownshop.service <<EOF
[Unit]
Description=unknown shop Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m bot
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=always
RestartSec=5
# Песочница: система только для чтения, запись — только в data/
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$APP_DIR/data
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable unknownshop
systemctl restart unknownshop
sleep 5
systemctl --no-pager status unknownshop | head -n 15
echo
echo "Готово. Логи: journalctl -u unknownshop -f"
