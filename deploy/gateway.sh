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

cat > /etc/caddy/Caddyfile.new <<EOF
{
	servers {
		# Только HTTP/1.1 и HTTP/2: HTTP/3 (UDP 443) клиентам API не нужен — минус открытый порт
		protocols h1 h2
		timeouts {
			# заголовки запроса — не дольше 10 с: «медленные» соединения не держат сервер
			read_header 10s
			# простаивающее keep-alive соединение закрывается через 2 минуты
			idle 2m
		}
	}
}

$HOST {
	header -Server
	reverse_proxy 127.0.0.1:8081 {
		# ответы моделей идут потоком — отдаём клиенту сразу, без буфера
		flush_interval -1
	}
}

# HTTP. Запросы к API не перенаправляем: ключ к этому моменту уже ушёл открытым текстом,
# а Anthropic SDK после редиректа слал бы его так на каждом запросе. Остальное — на HTTPS
http://$HOST, http://$IP {
	handle /v1* {
		header Content-Type "application/json; charset=utf-8"
		respond \`{"error": {"message": "Используйте https://$HOST/v1: по http ключ передаётся открытым текстом. Перевыпустите ключ в боте: Профиль → Перевыпустить ключ", "type": "invalid_request_error", "code": "https_required"}}\` 400
	}
	handle {
		redir https://$HOST{uri} 308
	}
}
EOF
# Проверяем новый конфиг до замены: с ошибкой в нём Caddy не поднялся бы после перезагрузки
caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

systemctl enable caddy
# reload не обрывает идущие потоки ответов; при первой установке — обычный запуск
systemctl reload-or-restart caddy

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
