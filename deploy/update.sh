#!/usr/bin/env bash
# Обновление кода бота на сервере. База (data/) и настройки (.env) не трогаются.
# Запуск: bash /opt/unknownshop/deploy/update.sh  (архив /root/update.tar.gz уже загружен)
set -euo pipefail
umask 022

APP_DIR=/opt/unknownshop
BACKUP_DIR=/root/backups

# Бэкап до распаковки: если он не получится, на сервере ничего не изменится. Через sqlite
# backup API копия целостная, даже если бот в этот момент пишет в базу
if [ -f "$APP_DIR/data/bot.db" ]; then
  echo "==> Бэкап базы"
  install -d -m 700 "$BACKUP_DIR"
  backup="$BACKUP_DIR/bot-$(date +%Y%m%d-%H%M%S).db"
  (
    umask 077
    "$APP_DIR/.venv/bin/python" -I - "$APP_DIR/data/bot.db" "$backup" <<'PY'
import sqlite3
import sys

src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
src.close()
check = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
if check != "ok":
    sys.exit(f"integrity_check: {check}")
PY
  )
  chmod 600 "$backup"
  echo "Бэкап: $backup"
fi

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
