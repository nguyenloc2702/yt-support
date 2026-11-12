"""HTTP server mini có hỗ trợ Range (cho Chromium phát mp4 trong IAB).

Chạy: python tools/serve_video_range.py <file.mp4> [port]  (mặc định port 8899)
Chỉ phục vụ đúng 1 file — dùng tạm để xem video, không dùng production.
"""
from __future__ import annotations

import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FILE_PATH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8899


_PLAYER_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Short preview</title><style>
body{margin:0;background:#111;display:flex;min-height:100vh;align-items:center;justify-content:center}
video{max-width:100%;max-height:100vh}
</style></head><body>
<video controls autoplay muted src="/video.mp4"></video>
</body></html>"""


class SingleFileHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = _PLAYER_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/video.mp4" or not FILE_PATH or not FILE_PATH.exists():
            self.send_error(404)
            return
        size = FILE_PATH.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(FILE_PATH, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError):
                    return  # client abort/seek — bình thường với video
                remaining -= len(chunk)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    if not FILE_PATH:
        print("usage: serve_video_range.py <file> [port]")
        sys.exit(1)
    ThreadingHTTPServer(("127.0.0.1", PORT), SingleFileHandler).serve_forever()
