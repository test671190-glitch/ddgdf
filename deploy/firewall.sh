#!/usr/bin/env bash
# Ограничения на один IP-адрес для веб-портов (80 и 443): защита от флуда подключениями.
# Это не защита от настоящего DDoS — тот забивает канал до сервера, её ставят перед сервером
# (Cloudflare, защищённый IP у хостера). SSH и остальные порты здесь не трогаются.
# Запуск: bash /opt/unknownshop/deploy/firewall.sh — можно повторять, правила переживают
# перезагрузку (сервис unknownshop-firewall).
set -euo pipefail

RULES=/etc/unknownshop-firewall.nft

cat > "$RULES" <<'EOF'
# Создаётся и удаляется сначала, чтобы повторная загрузка заменяла правила целиком
table inet unknownshop_guard
delete table inet unknownshop_guard

table inet unknownshop_guard {
	# адреса, превысившие частоту новых подключений (запись живёт минуту)
	set rate4 { type ipv4_addr; flags dynamic, timeout; timeout 1m; size 65536; }
	set rate6 { type ipv6_addr; flags dynamic, timeout; timeout 1m; size 65536; }
	# число одновременных соединений с адреса
	set conns4 { type ipv4_addr; flags dynamic; size 65536; }
	set conns6 { type ipv6_addr; flags dynamic; size 65536; }

	chain input {
		type filter hook input priority filter - 10; policy accept;
		iif lo accept
		tcp dport { 80, 443 } ct state new jump web_new
	}

	chain web_new {
		# не больше 100 новых подключений в секунду с одного адреса (всплеск до 200)
		meta nfproto ipv4 add @rate4 { ip saddr limit rate over 100/second burst 200 packets } counter drop
		meta nfproto ipv6 add @rate6 { ip6 saddr limit rate over 100/second burst 200 packets } counter drop
		# не больше 256 одновременных соединений с одного адреса
		meta nfproto ipv4 add @conns4 { ip saddr ct count over 256 } counter reject with tcp reset
		meta nfproto ipv6 add @conns6 { ip6 saddr ct count over 256 } counter reject with tcp reset
	}
}
EOF

# сначала проверка синтаксиса: с ошибкой правила не применятся вовсе
nft -c -f "$RULES"

cat > /etc/systemd/system/unknownshop-firewall.service <<EOF
[Unit]
Description=unknown shop: per-IP limits for web ports
Before=network-pre.target caddy.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f $RULES
ExecReload=/usr/sbin/nft -f $RULES
ExecStop=/usr/sbin/nft delete table inet unknownshop_guard

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable unknownshop-firewall >/dev/null
systemctl restart unknownshop-firewall
nft list table inet unknownshop_guard
