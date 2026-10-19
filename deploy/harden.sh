#!/usr/bin/env bash
# Жёсткая настройка сервера unknown shop. Можно запускать повторно.
# Запуск: bash /opt/unknownshop/deploy/harden.sh
#
# ВНИМАНИЕ: после запуска вход по SSH — только по ключу (пароль root перестаёт работать
# для SSH; в консоли хостера он работает как раньше). Перед запуском убедитесь, что ваш
# ключ есть в /root/.ssh/authorized_keys и вход по нему работает.
#
# Файрвол «всё закрыто, кроме 22/80/443» включается со страховкой: если не подтвердить
# доступ командой `systemctl stop ufw-rollback.timer` в течение 3 минут, он сам выключится.
set -euo pipefail
umask 022

APP_DIR=/opt/unknownshop

if [ ! -s /root/.ssh/authorized_keys ]; then
  echo "Нет /root/.ssh/authorized_keys — вход по паролю не отключаю, иначе потеряете доступ" >&2
  exit 1
fi
# Проверка песочницы ниже ждёт эндпоинт на 127.0.0.1:8081, а без PUBLIC_API_URL бот не
# запускается вовсе: песочница откатилась бы, и файрвол не включился бы
if ! grep -qE '^PUBLIC_API_URL=[^[:space:]]+' "$APP_DIR/.env"; then
  echo "В $APP_DIR/.env нет PUBLIC_API_URL — сначала запустите deploy/gateway.sh" >&2
  exit 1
fi

echo "==> SSH: только ключи"
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
# 00- раньше 50-cloud-init.conf: в sshd побеждает первое значение
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
EOF
sshd -t
systemctl reload ssh

echo "==> fail2ban: бан перебора паролей SSH"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban python3-systemd >/dev/null
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = true
bantime.maxtime = 1w
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "==> Ядро: сетевые и прочие защитные настройки"
cat > /etc/sysctl.d/90-hardening.conf <<'EOF'
net.ipv4.tcp_syncookies=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv6.conf.all.accept_redirects=0
net.ipv6.conf.default.accept_redirects=0
net.ipv4.conf.all.secure_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.conf.all.accept_source_route=0
net.ipv6.conf.all.accept_source_route=0
net.ipv4.icmp_echo_ignore_broadcasts=1
net.ipv4.icmp_ignore_bogus_error_responses=1
net.ipv4.tcp_rfc1337=1
kernel.kptr_restrict=2
kernel.dmesg_restrict=1
kernel.unprivileged_bpf_disabled=1
net.core.bpf_jit_harden=2
kernel.sysrq=0
fs.suid_dumpable=0
EOF
sysctl --system >/dev/null

echo "==> Журналы: хранить 30 дней, не больше 300 МБ"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=300M\nMaxRetentionSec=30day\n' \
  > /etc/systemd/journald.conf.d/90-retention.conf
systemctl restart systemd-journald

echo "==> Обновления безопасности: перезагрузка в 04:30, если её требует обновление ядра"
cat > /etc/apt/apt.conf.d/52unattended-local <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
EOF

echo "==> Бэкапы: доступ только root"
mkdir -p /root/backups
chmod 700 /root/backups
find /root/backups -type f -exec chmod 600 {} +

echo "==> Песочница бота: лимит памяти, без лишних прав и системных вызовов"
mkdir -p /etc/systemd/system/unknownshop.service.d
cat > /etc/systemd/system/unknownshop.service.d/hardening.conf <<'EOF'
[Service]
UMask=0077
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
ProcSubset=pid
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
RemoveIPC=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
SystemCallErrorNumber=EPERM
IPAddressDeny=169.254.0.0/16
# при утечке памяти (например, флуд огромными запросами) падает и перезапускается только бот
MemoryHigh=450M
MemoryMax=600M
TasksMax=200
LimitNOFILE=8192
EOF
systemctl daemon-reload
systemctl restart unknownshop
# бот поднимается секунд за 8–10: ждём эндпоинт до 40 секунд
up=0
for _ in $(seq 1 20); do
  sleep 2
  if systemctl is-active --quiet unknownshop && curl -fsS http://127.0.0.1:8081/health >/dev/null 2>&1; then
    up=1
    break
  fi
done
if [ "$up" != 1 ]; then
  echo "Бот не поднялся в песочнице — откатываю её" >&2
  journalctl -u unknownshop -n 30 --no-pager >&2
  rm -f /etc/systemd/system/unknownshop.service.d/hardening.conf
  systemctl daemon-reload
  systemctl restart unknownshop
  exit 1
fi

echo "==> Файрвол: всё входящее закрыто, кроме 22/80/443"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw >/dev/null
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
# страховка: без подтверждения через 3 минуты файрвол выключится сам
systemctl stop ufw-rollback.timer 2>/dev/null || true
systemd-run --quiet --unit=ufw-rollback --on-active=180 /usr/sbin/ufw --force disable
ufw --force enable >/dev/null
ufw status verbose
echo
echo "Проверьте НОВЫЙ вход по SSH и сайт, затем подтвердите: systemctl stop ufw-rollback.timer"
