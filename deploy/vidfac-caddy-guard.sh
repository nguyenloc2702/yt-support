#!/bin/bash
# Bảo đảm vidfac.gacdero.vn luôn có mặt trong Caddyfile của entre-tb.
#
# Vì sao cần: vidfac (video-system) chạy systemd trên host, nhưng TLS cho nó do
# container Caddy của project entre-tb kết thúc. Block vidfac từng bị xoá khỏi
# /opt/entre-tb/infra/caddy/Caddyfile (file này nằm trong repo khác, deploy của
# repo đó ghi đè). Caddy chỉ đọc file lúc khởi động, nên block biến mất âm thầm
# và chỉ lộ ra khi container Caddy restart — lúc đó vidfac chết HTTPS.
#
# Guard này chạy định kỳ: nếu thiếu block thì thêm lại và reload Caddy (reload
# graceful, không downtime site khác).
#
# Sửa tận gốc: thêm block vào repo entre-tb. Guard chỉ là lưới an toàn.

set -u

F=/opt/entre-tb/infra/caddy/Caddyfile
CONTAINER=entre-tb-prod-caddy-1
NETWORK=entre-tb-prod_frontend
LOG=/var/log/vidfac-caddy-guard.log

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

[ -f "$F" ] || exit 0
docker inspect "$CONTAINER" >/dev/null 2>&1 || exit 0

changed=0

if ! grep -q '^vidfac\.gacdero\.vn' "$F"; then
    GW=$(docker network inspect "$NETWORK" -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null)
    GW=${GW:-172.20.0.1}

    cp -p "$F" "$F.guard.bak"
    cat >>"$F" <<EOF

vidfac.gacdero.vn {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}

	# video-system chay systemd tren host, bind 0.0.0.0:8090.
	# ${GW} = gateway cua network ${NETWORK}.
	reverse_proxy ${GW}:8090
}
EOF
    log "thieu block -> da them vao Caddyfile (upstream ${GW}:8090)"
    changed=1
fi

# File có block nhưng config đang chạy có thể vẫn thiếu (chưa reload).
# Admin API 2019 KHÔNG publish ra host nên phải hỏi từ trong container.
if ! docker exec "$CONTAINER" wget -qO- --timeout=3 http://127.0.0.1:2019/config/ 2>/dev/null \
        | grep -q 'vidfac'; then
    changed=1
fi

[ "$changed" = "1" ] || exit 0

if ! docker exec "$CONTAINER" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    log "Caddyfile KHONG hop le sau khi sua — khoi phuc tu backup, khong reload"
    [ -f "$F.guard.bak" ] && cp -p "$F.guard.bak" "$F"
    exit 1
fi

docker exec "$CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
sleep 2

CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 \
    --resolve vidfac.gacdero.vn:443:127.0.0.1 https://vidfac.gacdero.vn/api/v1/health 2>/dev/null)

if [ "$CODE" = "200" ]; then
    log "reload OK — vidfac health 200"
else
    log "CANH BAO: reload xong nhung vidfac tra ${CODE:-000}"
fi

if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 300 ]; then
    tail -n 300 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
