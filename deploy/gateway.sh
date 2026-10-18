#!/usr/bin/env bash
# Включает собственный эндпоинт: HTTPS через Caddy + прокси бота на 127.0.0.1:8081.
# Запуск: bash /opt/unknownshop/deploy/gateway.sh
set -euo pipefail

APP_DIR=/opt/unknownshop
IP=154.83.140.29
HOST=154-83-140-29.sslip.io
PUBLIC_API_URL="https://$HOST/v1"

echo "==> Caddy"
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

cat > /etc/caddy/Caddyfile <<EOF
$HOST {
	reverse_proxy 127.0.0.1:8081 {
		# ответы моделей идут потоком — отдаём клиенту сразу, без буфера
		flush_interval -1
	}
}

# голый IP: перенаправляем на HTTPS-адрес (308 сохраняет POST)
http://$IP {
	redir https://$HOST{uri} 308
}
EOF

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

systemctl enable caddy
systemctl restart caddy

echo "==> Настройки бота"
cd "$APP_DIR"
if grep -q '^PUBLIC_API_URL=' .env; then
  sed -i "s#^PUBLIC_API_URL=.*#PUBLIC_API_URL=$PUBLIC_API_URL#" .env
else
  printf '\nPUBLIC_API_URL=%s\n' "$PUBLIC_API_URL" >> .env
fi
systemctl restart unknownshop

echo "==> Проверка (сертификат выпускается до минуты)"
for i in $(seq 1 20); do
  if curl -fsS "https://$HOST/health" >/dev/null 2>&1; then
    echo "OK: https://$HOST/health отвечает"
    curl -sS "https://$HOST/"; echo
    exit 0
  fi
  sleep 5
done
echo "Не дождался ответа. Логи: journalctl -u caddy -n 50 и journalctl -u unknownshop -n 50"
exit 1
