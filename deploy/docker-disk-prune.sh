#!/bin/bash
# Don image Docker cu tren VPS.
#
# Vi sao can timer rieng thay vi chi dua vao CI: image phinh ra la tu repo
# entre-tb (build 3-4 image/ngay, ~7.8GB/lan), ma CI cua repo do khong chay
# tren may nay. CI cua support-video-edit chi chay khi chinh no push. Timer
# nay doc lap voi moi repo nen moi project deu duoc don.
#
# Chi xoa image KHONG duoc container nao dung va cu hon 72h -> van du ban de
# rollback trong 3 ngay.

set -u

LOG=/var/log/docker-disk-prune.log
KEEP_HOURS="${KEEP_HOURS:-72}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

before_kb=$(df -Pk / | awk 'NR==2 {print $4}')

img_out=$(docker image prune -af --filter "until=${KEEP_HOURS}h" 2>&1 | tail -1)
cache_out=$(docker builder prune -af --filter "until=${KEEP_HOURS}h" 2>&1 | tail -1)

after_kb=$(df -Pk / | awk 'NR==2 {print $4}')
freed_mb=$(( (after_kb - before_kb) / 1024 ))
avail_mb=$(( after_kb / 1024 ))

log "image: ${img_out:-none} | cache: ${cache_out:-none} | +${freed_mb}MB | con trong ${avail_mb}MB"

# Canh bao som: con duoi 3GB thi ghi ro de con kip xu ly.
if [ "$avail_mb" -lt 3072 ]; then
    log "CANH BAO: chi con ${avail_mb}MB — kiem tra /tmp, data/objects, image dang dung"
fi

if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 300 ]; then
    tail -n 300 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
