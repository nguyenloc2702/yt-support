"""Affiliate link service — deeplink builders (ClickBank/eBay tự dựng URL),
slug redirect + click tracking, bio page, và cron đồng bộ doanh thu từ
Reporting API của từng mạng (ClickBank Reporting, eBay EPN TDR).

Mọi network đo attribution chung một chìa khoá: subid = job_{item_id}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable

import httpx

from .config import (
    clickbank_api_base,
    clickbank_api_key,
    clickbank_nickname,
    ebay_campaign_id,
    ebay_dev_key,
    epn_tdr_url,
    public_base_url,
)
from .store import Store, new_id, now
from .webhooks import WebhookService

logger = logging.getLogger("video-system.affiliate")

NETWORKS = ("clickbank", "ebay")
SUBID_MAX = 24  # ClickBank tid limit (alphanumeric)


class AffiliateError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "affiliate_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def make_subid(item_id: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]", "", f"job{item_id}")
    return raw[:SUBID_MAX]


def build_clickbank_link(cb_account: str, cb_promoter: str, tid: str) -> str:
    """https://{aff}.{vendor}.hop.clickbank.net/?tid={tid} — tid cắt ≤24 alnum."""
    if not cb_account or not cb_promoter:
        raise AffiliateError(
            "Thiếu CLICKBANK_NICKNAME hoặc promoter của vendor trong sản phẩm.",
            code="clickbank_not_configured",
        )
    clean = re.sub(r"[^a-zA-Z0-9]", "", tid)[:SUBID_MAX]
    base = f"https://{cb_account}.{cb_promoter}.hop.clickbank.net/"
    if clean:
        base += f"?tid={clean}"
    return base


def build_ebay_link(item_url: str, campaign_id: str, customid: str) -> str:
    """Thêm campid + customid + mkevt=1 vào URL item eBay."""
    if not item_url or not campaign_id:
        raise AffiliateError(
            "Thiếu EBAY_CAMPAIGN_ID hoặc URL sản phẩm eBay.",
            code="ebay_not_configured",
        )
    sep = "&" if "?" in item_url else "?"
    parts = [f"campid={campaign_id}", "mkevt=1"]
    if customid:
        parts.append(f"customid={customid}")
    return f"{item_url}{sep}{'&'.join(parts)}"


def _new_slug() -> str:
    return uuid.uuid4().hex[:8]


class AffiliateService:
    def __init__(self, store: Store, webhooks: WebhookService | None = None,
                 http_factory: Callable[[], httpx.Client] | None = None):
        self.store = store
        self.webhooks = webhooks
        self._http_factory = http_factory or (lambda: httpx.Client(timeout=60.0))

    def _emit_event(self, event_type: str, item_id: str | None, payload: dict[str, Any]) -> None:
        if not self.webhooks:
            return
        ev = {
            "id": new_id("evt"),
            "item_id": item_id,
            "shot_id": None,
            "type": event_type,
            "actor": "affiliate",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "at": now(),
        }
        self.store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) VALUES (?,?,?,?,?,?,?)",
            (ev["id"], ev["item_id"], ev["shot_id"], ev["type"], ev["actor"], ev["payload_json"], ev["at"]),
        )
        self.webhooks.dispatch_event(ev)

    # -- products ---------------------------------------------------------------

    @staticmethod
    def _sanitize_product(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        d["is_active"] = bool(row.get("is_active", 1))
        return d

    def create_product(
        self,
        *,
        network: str,
        label: str,
        cb_promoter: str = "",
        ebay_item_url: str = "",
        is_active: bool = True,
    ) -> dict[str, Any]:
        network = (network or "").strip().lower()
        if network not in NETWORKS:
            raise AffiliateError(f"network phải là một trong {NETWORKS}.", status_code=422)
        if not (label or "").strip():
            raise AffiliateError("label không được trống.", status_code=422)
        if network == "clickbank" and not (cb_promoter or "").strip():
            raise AffiliateError("ClickBank product cần promoter (nickname của vendor).", status_code=422)
        if network == "ebay" and not (ebay_item_url or "").strip():
            raise AffiliateError("eBay product cần URL item.", status_code=422)
        pid = new_id("apd")
        t = now()
        self.store.execute(
            "INSERT INTO affiliate_products (id,network,label,cb_promoter,ebay_item_url,is_active,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, network, label.strip(), (cb_promoter or "").strip(), (ebay_item_url or "").strip(),
             1 if is_active else 0, t, t),
        )
        row = self.store.query_one("SELECT * FROM affiliate_products WHERE id=?", (pid,))
        return self._sanitize_product(row) or {}

    def list_products(self, include_inactive: bool = True) -> list[dict[str, Any]]:
        if include_inactive:
            rows = self.store.query("SELECT * FROM affiliate_products ORDER BY created_at DESC")
        else:
            rows = self.store.query("SELECT * FROM affiliate_products WHERE is_active=1 ORDER BY created_at DESC")
        return [self._sanitize_product(r) or {} for r in rows]

    def update_product(
        self,
        product_id: str,
        *,
        label: str | None = None,
        cb_promoter: str | None = None,
        ebay_item_url: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM affiliate_products WHERE id=?", (product_id,))
        if not row:
            raise AffiliateError(f"Không tìm thấy sản phẩm {product_id}.", status_code=404)
        sets: list[str] = []
        vals: list[Any] = []
        if label is not None:
            if not label.strip():
                raise AffiliateError("label không được trống.", status_code=422)
            sets.append("label=?")
            vals.append(label.strip())
        if cb_promoter is not None:
            sets.append("cb_promoter=?")
            vals.append(cb_promoter.strip())
        if ebay_item_url is not None:
            sets.append("ebay_item_url=?")
            vals.append(ebay_item_url.strip())
        if is_active is not None:
            sets.append("is_active=?")
            vals.append(1 if is_active else 0)
        if sets:
            sets.append("updated_at=?")
            vals.append(now())
            vals.append(product_id)
            self.store.execute(f"UPDATE affiliate_products SET {', '.join(sets)} WHERE id=?", tuple(vals))
        updated = self.store.query_one("SELECT * FROM affiliate_products WHERE id=?", (product_id,))
        return self._sanitize_product(updated) or {}

    def delete_product(self, product_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM affiliate_products WHERE id=?", (product_id,))
        if not row:
            raise AffiliateError(f"Không tìm thấy sản phẩm {product_id}.", status_code=404)
        self.store.execute("DELETE FROM affiliate_products WHERE id=?", (product_id,))
        return {"deleted": True, "id": product_id}

    # -- links ------------------------------------------------------------------

    def attach_product(self, item_id: str, product_id: str, *, actor: str = "api") -> dict[str, Any]:
        """Sinh deeplink cho product gắn vào job; mỗi (product,item) 1 link/slug."""
        product = self.store.query_one("SELECT * FROM affiliate_products WHERE id=?", (product_id,))
        if not product:
            raise AffiliateError(f"Không tìm thấy sản phẩm {product_id}.", status_code=404)
        if not product.get("is_active", 1):
            raise AffiliateError("Sản phẩm đang ở trạng thái inactive.", status_code=422)
        network = product["network"]
        subid = make_subid(item_id)
        if network == "clickbank":
            cb_account = clickbank_nickname()
            deeplink = build_clickbank_link(cb_account, product.get("cb_promoter") or "", subid)
        elif network == "ebay":
            deeplink = build_ebay_link(product.get("ebay_item_url") or "", ebay_campaign_id(), subid)
        else:
            raise AffiliateError(f"Network không hỗ trợ: {network}", status_code=422)

        existing = self.store.query_one(
            "SELECT * FROM affiliate_links WHERE product_id=? AND item_id=? ORDER BY created_at DESC LIMIT 1",
            (product_id, item_id),
        )
        if existing and existing["deeplink"] == deeplink:
            return self._sanitize_link(existing)

        slug = _new_slug()
        while self.store.query_one("SELECT id FROM affiliate_links WHERE slug=?", (slug,)):
            slug = _new_slug()
        t = now()
        link_id = new_id("aln")
        self.store.execute(
            "INSERT INTO affiliate_links (id,product_id,item_id,network,deeplink,slug,subid,clicks,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,?,?)",
            (link_id, product_id, item_id, network, deeplink, slug, subid, t, t),
        )
        row = self.store.query_one("SELECT * FROM affiliate_links WHERE id=?", (link_id,))
        self._emit_event("affiliate.link.attached", item_id, {"slug": slug, "network": network, "actor": actor})
        return self._sanitize_link(row)

    @staticmethod
    def _sanitize_link(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        base_url = public_base_url()
        return {
            "id": row["id"],
            "product_id": row["product_id"],
            "item_id": row.get("item_id"),
            "network": row["network"],
            "deeplink": row["deeplink"],
            "slug": row["slug"],
            "url": f"{base_url}/b/{row['slug']}",
            "subid": row.get("subid"),
            "clicks": int(row.get("clicks") or 0),
            "last_click_at": row.get("last_click_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def list_links(self, item_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if item_id:
            rows = self.store.query(
                "SELECT * FROM affiliate_links WHERE item_id=? ORDER BY created_at DESC LIMIT ?",
                (item_id, max(1, min(500, limit))),
            )
        else:
            rows = self.store.query(
                "SELECT * FROM affiliate_links ORDER BY created_at DESC LIMIT ?",
                (max(1, min(500, limit)),),
            )
        return [self._sanitize_link(r) or {} for r in rows]

    def record_click(self, slug: str, *, referer: str = "", user_agent: str = "") -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM affiliate_links WHERE slug=?", (slug,))
        if not row:
            raise AffiliateError(f"Link không tồn tại: /b/{slug}", status_code=404)
        self.store.execute(
            "UPDATE affiliate_links SET clicks=clicks+1, last_click_at=?, updated_at=? WHERE id=?",
            (now(), now(), row["id"]),
        )
        self._emit_event(
            "affiliate.link.clicked",
            row.get("item_id"),
            {"slug": slug, "network": row["network"], "referer": referer[:200], "user_agent": user_agent[:200]},
        )
        return {"slug": slug, "deeplink": row["deeplink"], "item_id": row.get("item_id"), "network": row["network"]}

    # -- bio page ----------------------------------------------------------------

    def get_bio_page(self, handle: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM bio_pages WHERE handle=?", (handle,))
        manual = json.loads(row["items_json"]) if row and row.get("items_json") else []
        return {
            "handle": handle,
            "title": (row.get("title") if row else "") or "",
            "items": manual,
            "base_url": public_base_url(),
        }

    def set_bio_page(self, handle: str, *, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        clean: list[dict[str, Any]] = []
        for it in items[:30]:
            slug = (it.get("slug") or "").strip()
            if not slug:
                continue
            clean.append({"label": (it.get("label") or "").strip()[:120], "slug": slug})
        self.store.execute(
            "INSERT INTO bio_pages (handle,title,items_json,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(handle) DO UPDATE SET title=excluded.title, items_json=excluded.items_json, "
            "updated_at=excluded.updated_at",
            (handle, (title or "").strip(), json.dumps(clean, ensure_ascii=False), now()),
        )
        return self.get_bio_page(handle)

    def render_bio_items(self, handle: str) -> list[dict[str, str]]:
        """Trang bio = items thủ công trước, sau đó tự bơm link mới nhất chưa có."""
        page = self.get_bio_page(handle)
        used_slugs = {it["slug"] for it in page["items"]}
        out = [
            {"label": it["label"] or f"/b/{it['slug']}", "url": f"{page['base_url']}/b/{it['slug']}"}
            for it in page["items"]
        ]
        auto = self.store.query(
            "SELECT * FROM affiliate_links ORDER BY created_at DESC LIMIT 20"
        )
        for l in auto:
            if l["slug"] in used_slugs:
                continue
            product = self.store.query_one(
                "SELECT label FROM affiliate_products WHERE id=?", (l["product_id"],)
            )
            label = (product.get("label") if product else "") or f"{l['network']} deal"
            out.append({"label": label, "url": f"{page['base_url']}/b/{l['slug']}"})
        return out[:30]

    # -- conversions sync (reporting API từng mạng) ------------------------------

    def list_conversions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT * FROM affiliate_conversions ORDER BY event_date DESC, synced_at DESC LIMIT ?",
            (max(1, min(500, limit)),),
        )
        return [dict(r) for r in rows]

    def _upsert_conversion(self, entry: dict[str, Any]) -> bool:
        """entry: {network, network_txn_id, subid, item_id, product_label, amount, currency, status, event_date}.
        Returns True nếu là mới. Row đã có nhưng status/amount đổi (CLEARED->RETURNED, refund...)
        thì UPDATE — doanh thu phải net lại, không được đóng băng ở pending."""
        subid = entry.get("subid") or ""
        item_id = entry.get("item_id")
        if not item_id and subid:
            link = self.store.query_one(
                "SELECT item_id FROM affiliate_links WHERE subid=? LIMIT 1", (subid,)
            )
            item_id = link.get("item_id") if link else None
        new_amount = float(entry.get("amount") or 0.0)
        new_currency = entry.get("currency") or "USD"
        existing = self.store.query_one(
            "SELECT id,status,amount,currency,item_id FROM affiliate_conversions "
            "WHERE network=? AND network_txn_id=?",
            (entry["network"], entry["network_txn_id"]),
        )
        if existing:
            # product_label cố tình KHÔNG nằm trong detection lẫn UPDATE: label
            # chỉ là cosmetic và nguồn sync không có ORDER BY ổn định (CB
            # fallback products[0]) — đưa vào detection sẽ flap mỗi lần sync.
            changed = (
                (existing["status"] or "") != (entry.get("status") or "")
                or abs(float(existing["amount"] or 0.0) - new_amount) > 1e-9
                or (existing["currency"] or "USD") != new_currency
                or (existing["item_id"] or None) != (item_id or None)
            )
            if changed:
                self.store.execute(
                    "UPDATE affiliate_conversions SET status=?, amount=?, currency=?, "
                    "item_id=?, synced_at=? WHERE network=? AND network_txn_id=?",
                    (
                        entry.get("status"), new_amount, new_currency, item_id, now(),
                        entry["network"], entry["network_txn_id"],
                    ),
                )
                logger.info(
                    "affiliate conversion updated: %s/%s status=%s amount=%s currency=%s",
                    entry["network"], entry["network_txn_id"], entry.get("status"), new_amount, new_currency,
                )
            return False
        self.store.execute(
            "INSERT OR IGNORE INTO affiliate_conversions "
            "(id,network,network_txn_id,subid,item_id,product_label,amount,currency,status,event_date,synced_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("acv"), entry["network"], entry["network_txn_id"], subid or None,
                item_id, entry.get("product_label"), new_amount,
                new_currency, entry.get("status"), entry.get("event_date"), now(),
            ),
        )
        return True

    def sync_clickbank(self, days: int = 7) -> dict[str, Any]:
        """ClickBank Reporting API (XML). Endpoint/base qua CLICKBANK_API_BASE."""
        key = clickbank_api_key()
        if not key:
            return {"network": "clickbank", "skipped": "CLICKBANK_API_KEY unset", "new": 0}
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, min(31, days)))
        url = f"{clickbank_api_base()}/reports/TRANSACTIONS/XML"
        params = {
            "startdate": start.strftime("%m/%d/%Y"),
            "enddate": end.strftime("%m/%d/%Y"),
        }
        try:
            with self._http_factory() as client:
                resp = client.get(url, params=params, headers={"Authorization": key})
        except httpx.HTTPError as exc:
            logger.warning("clickbank sync network error: %s", exc)
            return {"network": "clickbank", "error": str(exc), "new": 0}
        if resp.status_code != 200:
            logger.warning("clickbank sync HTTP %s: %s", resp.status_code, resp.text[:200])
            return {"network": "clickbank", "error": f"HTTP {resp.status_code}", "new": 0}

        new_count = 0
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("clickbank sync XML parse error: %s", exc)
            return {"network": "clickbank", "error": f"XML parse: {exc}", "new": 0}

        for txn in root.iter("transaction"):
            data = {child.tag.lower(): (child.text or "").strip() for child in txn}
            txn_id = data.get("transid") or data.get("transactionid") or ""
            if not txn_id:
                continue
            products = self.store.query(
                "SELECT label FROM affiliate_products WHERE network='clickbank'"
            )
            new_count += int(
                self._upsert_conversion(
                    {
                        "network": "clickbank",
                        "network_txn_id": txn_id,
                        "subid": data.get("tid") or data.get("trackingid") or "",
                        "product_label": data.get("title") or (products[0]["label"] if products else None),
                        "amount": data.get("amount"),
                        "currency": data.get("currency") or "USD",
                        "status": (data.get("status") or "").lower() or "pending",
                        "event_date": data.get("transdate") or end.strftime("%Y-%m-%d"),
                    }
                )
            )
        if new_count:
            self._emit_event("affiliate.conversion.synced", None, {"network": "clickbank", "new": new_count})
        return {"network": "clickbank", "new": new_count}

    def sync_ebay(self, days: int = 7) -> dict[str, Any]:
        """eBay Partner Network TDR API. Endpoint + auth env-override
        (xác minh lại khi account thật — EPN yêu cầu enable API access)."""
        key = ebay_dev_key()
        if not key:
            return {"network": "ebay", "skipped": "EBAY_DEV_KEY unset", "new": 0}
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, min(31, days)))
        params = {
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
        }
        try:
            with self._http_factory() as client:
                resp = client.get(
                    epn_tdr_url(), params=params,
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("epn sync network error: %s", exc)
            return {"network": "ebay", "error": str(exc), "new": 0}
        if resp.status_code != 200:
            logger.warning("epn sync HTTP %s: %s", resp.status_code, resp.text[:200])
            return {"network": "ebay", "error": f"HTTP {resp.status_code}", "new": 0}

        new_count = 0
        try:
            body = resp.json()
        except ValueError:
            return {"network": "ebay", "error": "non-JSON response", "new": 0}
        # Defensive: tìm list transaction ở các shape phổ biến
        rows = body if isinstance(body, list) else (
            body.get("transactions") or body.get("items") or body.get("data") or []
        )
        for t in rows if isinstance(rows, list) else []:
            if not isinstance(t, dict):
                continue
            txn_id = str(
                t.get("transactionId") or t.get("transaction_id") or t.get("id") or ""
            )
            if not txn_id:
                continue
            amount = t.get("commission") or t.get("earnings") or t.get("amount") or 0.0
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                amount = 0.0
            new_count += int(
                self._upsert_conversion(
                    {
                        "network": "ebay",
                        "network_txn_id": txn_id,
                        "subid": str(t.get("customid") or t.get("customId") or t.get("custom_id") or ""),
                        "product_label": t.get("itemTitle") or t.get("title"),
                        "amount": amount,
                        "currency": t.get("currency") or "USD",
                        "status": (t.get("status") or "pending").lower(),
                        "event_date": t.get("transactionDate") or t.get("date") or end.strftime("%Y-%m-%d"),
                    }
                )
            )
        if new_count:
            self._emit_event("affiliate.conversion.synced", None, {"network": "ebay", "new": new_count})
        return {"network": "ebay", "new": new_count}

    def sync_all(self, days: int = 7) -> list[dict[str, Any]]:
        results = []
        try:
            results.append(self.sync_clickbank(days=days))
        except Exception as exc:  # noqa: BLE001 — sync không được chết scheduler
            logger.exception("affiliate sync clickbank failed: %s", exc)
            results.append({"network": "clickbank", "error": str(exc), "new": 0})
        try:
            results.append(self.sync_ebay(days=days))
        except Exception as exc:  # noqa: BLE001
            logger.exception("affiliate sync ebay failed: %s", exc)
            results.append({"network": "ebay", "error": str(exc), "new": 0})
        return results


_service: AffiliateService | None = None


def set_affiliate_service(svc: AffiliateService) -> None:
    global _service
    _service = svc


def get_affiliate_service() -> AffiliateService:
    if _service is None:
        raise AffiliateError("Affiliate service chưa được khởi tạo.", status_code=503, code="affiliate_not_ready")
    return _service


def start_affiliate_sync_scheduler(store: Store, webhooks: WebhookService) -> threading.Thread | None:
    """Cron đồng bộ doanh thu: mặc định mỗi 6h (AFFILIATE_SYNC_INTERVAL_MIN).
    Chỉ chạy khi có ít nhất một API key configured."""
    try:
        interval_min = int(os.environ.get("AFFILIATE_SYNC_INTERVAL_MIN", "360").strip() or "360")
    except ValueError:
        interval_min = 360
    interval_s = max(300, interval_min * 60)
    if not (clickbank_api_key() or ebay_dev_key()):
        logger.info("affiliate sync disabled (no CLICKBANK_API_KEY / EBAY_DEV_KEY)")
        return None
    svc = AffiliateService(store, webhooks)
    set_affiliate_service(svc)

    def _loop() -> None:
        while True:
            try:
                results = svc.sync_all()
                for r in results:
                    logger.info("affiliate sync %s", r)
            except Exception as exc:  # noqa: BLE001
                logger.exception("affiliate sync loop error: %s", exc)
            time.sleep(interval_s)

    th = threading.Thread(target=_loop, name="affiliate-sync", daemon=True)
    th.start()
    return th
