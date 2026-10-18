#!/usr/bin/env bash
# Обновление кода бота на сервере. База (data/) и настройки (.env) не трогаются.
# Запуск: bash /opt/unknownshop/deploy/update.sh  (архив /root/update.tar.gz уже загружен)
set -euo pipefail
umask 022

APP_DIR=/opt/unknownshop
# Архив собирается на Windows с правами 666/777. Без этих флагов root распаковал бы код
# с правом записи для любого пользователя сервера, а бот потом выполнил бы чужой код
tar --no-same-owner --no-same-permissions -xzf /root/update.tar.gz -C "$APP_DIR"
chmod -R go-w "$APP_DIR"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
if ! id unknownshop >/dev/null 2>&1; then
  echo "⚠️ Бот всё ещё работает от root. Запустите один раз: bash $APP_DIR/deploy/setup.sh"
fi
systemctl restart unknownshop
sleep 4
systemctl --no-pager status unknownshop | head -n 8
