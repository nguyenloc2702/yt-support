#!/bin/bash
# Dọn rác build video còn sót trong /tmp.
#
# Vì sao không dùng thẳng systemd-tmpfiles: policy mặc định là 10 ngày, và nếu
# hạ xuống 2 ngày thì nó xoá cả socket của X11/VNC đang chạy trên máy này
# (/tmp/.X11-unix) lẫn các thư mục private của systemd. Script này chỉ xoá
# THƯ MỤC, bỏ qua mọi thứ bắt đầu bằng dấu chấm và các tiền tố hệ thống, đồng
# thời bỏ qua thư mục đang được một process dùng làm cwd.
#
# Dùng: video-tmp-sweep [số_ngày]     (mặc định 2)

set -u

DAYS="${1:-2}"
LOG=/var/log/video-tmp-sweep.log
DRY_RUN="${DRY_RUN:-0}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# Thư mục đang được process nào đó dùng làm cwd -> không xoá.
in_use() {
    local target="$1" pid cwd
    for pid in /proc/[0-9]*; do
        cwd=$(readlink "$pid/cwd" 2>/dev/null) || continue
        case "$cwd" in
            "$target"|"$target"/*) return 0 ;;
        esac
    done
    return 1
}

removed=0
freed_kb=0

while IFS= read -r dir; do
    name=$(basename "$dir")

    # Bỏ qua thư mục ẩn và thư mục hệ thống.
    case "$name" in
        .*|systemd-private-*|snap-private-*|systemd-*|snap.*) continue ;;
    esac

    if in_use "$dir"; then
        log "skip (đang dùng): $dir"
        continue
    fi

    size_kb=$(du -sk "$dir" 2>/dev/null | awk '{print $1}')
    size_kb=${size_kb:-0}

    if [ "$DRY_RUN" = "1" ]; then
        log "would-remove: $dir (${size_kb}KB)"
    else
        if rm -rf -- "$dir"; then
            log "removed: $dir (${size_kb}KB)"
        else
            log "FAILED: $dir"
            continue
        fi
    fi

    removed=$((removed + 1))
    freed_kb=$((freed_kb + size_kb))
done < <(find /tmp -mindepth 1 -maxdepth 1 -type d -mtime "+$DAYS" 2>/dev/null)

avail=$(df -Pk / | awk 'NR==2 {print int($4 / 1024)}')
log "done: ${removed} thư mục, giải phóng $((freed_kb / 1024))MB, còn trống ${avail}MB"

# Giữ log gọn: chỉ 500 dòng cuối.
if [ -f "$LOG" ] && [ "$(wc -l <"$LOG")" -gt 500 ]; then
    tail -n 500 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
