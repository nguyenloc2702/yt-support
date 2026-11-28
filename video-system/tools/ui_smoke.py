"""ui_smoke.py — headless browser smoke + theme audit cho MPA frontend.

Muc dich: nghiem thu Phase 1 (page load duoc, khong loi console) va Phase 2
(dark theme: khong con surface sang, contrast chu du doc).

Chay:
    VIDEO_WEB_PIN=997220 .venv/Scripts/python.exe tools/ui_smoke.py \
        --base http://127.0.0.1:8011 --out out/ui_smoke

Xuat:
    <out>/<page>.png            screenshot full-page
    <out>/report.json           console errors, failed requests, light surfaces
    <out>/report.md             ban doc nhanh
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PAGES = ["/todos", "/shorts", "/youtube", "/publish", "/chat", "/library"]

# JS chay trong page: tim element co background sang (dau hieu con theme cu)
_AUDIT_JS = r"""
() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x.trim()));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = ({r, g, b}) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const lightSurfaces = [];
  const lowContrast = [];
  const all = document.querySelectorAll('body *');
  let checked = 0;
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 24 || r.height < 12) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) continue;
    const bg = parse(st.backgroundColor);
    if (bg && bg.a > 0.6 && lum(bg) > 0.72) {
      const txt = (el.textContent || '').trim().slice(0, 60);
      lightSurfaces.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 80),
        bg: st.backgroundColor,
        text: txt,
        w: Math.round(r.width), h: Math.round(r.height),
      });
    }
    // contrast chi kiem cho node co text truc tiep
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (own && bg) {
      const fg = parse(st.color);
      if (fg && fg.a > 0.5) {
        checked++;
        const cr = ratio(fg, bg.a < 0.6 ? {r: 11, g: 18, b: 32} : bg);
        if (cr < 3.0) {
          lowContrast.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 80),
            fg: st.color, bg: st.backgroundColor,
            ratio: Math.round(cr * 100) / 100,
            text: (el.textContent || '').trim().slice(0, 50),
          });
        }
      }
    }
  }
  return {
    title: document.title,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    bodyColor: getComputedStyle(document.body).color,
    scrollH: document.documentElement.scrollHeight,
    lightSurfaces: lightSurfaces.slice(0, 40),
    lowContrast: lowContrast.slice(0, 40),
    contrastChecked: checked,
  };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--out", default="out/ui_smoke")
    ap.add_argument("--pin", default=os.environ.get("VIDEO_WEB_PIN", "997220"))
    ap.add_argument("--only", default="", help="comma-separated paths")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = [p.strip() for p in args.only.split(",") if p.strip()] or PAGES

    report = {"base": args.base, "pages": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  device_scale_factor=1)
        # login qua API de lay cookie session
        r = ctx.request.post(f"{args.base}/api/v1/auth/login", data={"pin": args.pin})
        if r.status != 200:
            print(f"LOGIN FAILED {r.status}: {r.text()[:200]}", file=sys.stderr)
            return 2
        for path in pages:
            entry = {"console": [], "pageerrors": [], "failed": [], "http4xx5xx": []}
            page = ctx.new_page()
            page.on("console", lambda m, e=entry: e["console"].append(
                {"type": m.type, "text": m.text[:300]}) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda exc, e=entry: e["pageerrors"].append(str(exc)[:400]))
            page.on("requestfailed", lambda req, e=entry: e["failed"].append(
                {"url": req.url[:200], "err": (req.failure or "")[:120]}))
            page.on("response", lambda resp, e=entry: e["http4xx5xx"].append(
                {"status": resp.status, "url": resp.url[:160]})
                if resp.status >= 400 else None)
            try:
                page.goto(f"{args.base}{path}", wait_until="networkidle", timeout=45000)
            except Exception as exc:  # noqa: BLE001
                entry["goto_error"] = str(exc)[:300]
            page.wait_for_timeout(1200)
            try:
                entry["audit"] = page.evaluate(_AUDIT_JS)
            except Exception as exc:  # noqa: BLE001
                entry["audit_error"] = str(exc)[:300]
            shot = out / f"{path.strip('/').replace('/', '_') or 'root'}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
                entry["screenshot"] = str(shot)
            except Exception as exc:  # noqa: BLE001
                entry["shot_error"] = str(exc)[:200]
            report["pages"][path] = entry
            page.close()
        browser.close()

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# UI smoke — {args.base}", ""]
    for path, e in report["pages"].items():
        a = e.get("audit") or {}
        lines.append(f"## {path}")
        lines.append(f"- title: {a.get('title', '?')}")
        lines.append(f"- body bg/fg: `{a.get('bodyBg')}` / `{a.get('bodyColor')}`")
        lines.append(f"- scroll height: {a.get('scrollH')}")
        lines.append(f"- console errors: {len([c for c in e['console'] if c['type'] == 'error'])}"
                     f" · pageerrors: {len(e['pageerrors'])} · failed req: {len(e['failed'])}"
                     f" · http>=400: {len(e['http4xx5xx'])}")
        lines.append(f"- surface sáng (>0.72 lum): {len(a.get('lightSurfaces') or [])}")
        lines.append(f"- text contrast < 3.0: {len(a.get('lowContrast') or [])}"
                     f" (đã đo {a.get('contrastChecked', 0)} node)")
        for c in e["console"][:6]:
            lines.append(f"  - console[{c['type']}]: {c['text'][:160]}")
        for p in e["pageerrors"][:6]:
            lines.append(f"  - pageerror: {p[:200]}")
        for f in e["failed"][:6]:
            lines.append(f"  - failed: {f['url'][:120]} ({f['err']})")
        for s in (a.get("lightSurfaces") or [])[:8]:
            lines.append(f"  - light-surface `<{s['tag']} class={s['cls']}>` bg={s['bg']} text={s['text'][:40]!r}")
        for c in (a.get("lowContrast") or [])[:8]:
            lines.append(f"  - low-contrast `<{c['tag']} class={c['cls']}>` {c['fg']} on {c['bg']} = {c['ratio']} text={c['text'][:40]!r}")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
