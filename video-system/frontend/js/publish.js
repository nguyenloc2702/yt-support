// publish.js — instagram + affiliate
let igStateCache = { status: null, publishes: [], products: [], links: [], conversions: [] };
let igPollTimers = {};

function goPublishPage() {
  setActiveNav('publish');
  if ((window.location.hash || '') !== '#/publish') {
    suppressHashSync = true;
    window.location.hash = '#/publish';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  openPublishPage();
}

function openPublishPage() {
  setActiveNav('publish');
  hideTodoDetailDom();
  hideShortsPage();
  hideYoutubePage();
  hideChatPage();
  document.getElementById('pageTodoList')?.classList.add('hidden');
  const page = document.getElementById('pagePublish');
  if (!page) return;
  page.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'auto' });
  const h = window.location.hash || '';
  if (h.includes('ig=connected')) {
    showToast('✅ Đã kết nối tài khoản Instagram thành công!', 'ok', 4000);
    if (history.replaceState) history.replaceState(null, '', '#/publish');
  } else if (h.includes('ig=error')) {
    const m = h.match(/reason=([^&]+)/);
    const reason = m ? decodeURIComponent(m[1]) : 'OAuth thất bại';
    showToast(`❌ Lỗi kết nối Instagram: ${reason}`, 'err', 7000);
    if (history.replaceState) history.replaceState(null, '', '#/publish');
  }
  loadPublishDashboard();
}

async function loadPublishDashboard() {
  const page = document.getElementById('pagePublish');
  if (!page) return;
  page.innerHTML = `<section class="panel"><div style="padding:16px;color:var(--fg-muted)">⏳ Đang tải Publish dashboard...</div></section>`;
  try {
    const rs = await apiFetch('/api/v1/instagram/status');
    igStateCache.status = await rs.json();
    if (igStateCache.status?.connected) {
      const rp = await apiFetch('/api/v1/instagram/publishes?limit=50');
      if (rp.ok) igStateCache.publishes = (await rp.json()).publishes || [];
    } else {
      igStateCache.publishes = [];
    }
    const [rp, rl, rc] = await Promise.all([
      apiFetch('/api/v1/affiliate/products'),
      apiFetch('/api/v1/affiliate/links?limit=50'),
      apiFetch('/api/v1/affiliate/conversions?limit=50'),
    ]);
    if (rp.ok) igStateCache.products = (await rp.json()).products || [];
    if (rl.ok) igStateCache.links = (await rl.json()).links || [];
    if (rc.ok) igStateCache.conversions = (await rc.json()).conversions || [];
    renderPublishDashboard();
  } catch (err) {
    page.innerHTML = `<section class="panel"><div style="padding:16px;color:var(--danger)">❌ Lỗi tải Publish dashboard: ${esc(err.message)}</div></section>`;
  }
}

function renderPublishDashboard() {
  const page = document.getElementById('pagePublish');
  if (!page) return;
  const st = igStateCache.status || {};
  const acc = st.account;

  let igCard = '';
  if (!st.configured) {
    igCard = `
      <div style="padding:14px;border-radius:10px;border:1px dashed var(--warn);background:rgba(255,193,7,.08)">
        <div style="font-weight:700;margin-bottom:6px">⚙️ Chưa cấu hình Instagram OAuth trên máy chủ</div>
        <div style="font-size:12px;color:var(--fg-muted);line-height:1.6">
          Thêm <code>INSTAGRAM_APP_ID</code>, <code>INSTAGRAM_APP_SECRET</code>, <code>IG_TOKEN_ENCRYPTION_KEY</code> vào <code>.env</code>.<br>
          Redirect URI trên Meta App: <code>${esc(st.redirect_uri || '')}</code>
        </div>
      </div>`;
  } else if (!st.connected || !acc) {
    igCard = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-weight:700;font-size:15px">Chưa liên kết tài khoản Instagram</div>
          <div style="font-size:12px;color:var(--fg-muted)">Yêu cầu: tài khoản Instagram Business/Creator. Bấm Kết nối và cấp quyền đăng bài.</div>
        </div>
        <button type="button" class="btn-primary" onclick="startInstagramConnect()">🔗 Kết nối Instagram</button>
      </div>`;
  } else {
    const q = st.quota || {};
    igCard = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <strong style="font-size:16px">@${esc(acc.username || '')}</strong>
            <span class="pill pill-ghost mono">${esc(acc.account_type || '')}</span>
            <span class="pill pill-ok">✅ Đã kết nối</span>
          </div>
          <div style="font-size:12px;color:var(--fg-muted);margin-top:4px">
            Bài đã đăng: <strong>${Number(acc.media_count || 0).toLocaleString('vi-VN')}</strong> ·
            Quota 24h: <strong class="mono">${q.quota_remaining !== null && q.quota_remaining !== undefined ? `${q.quota_remaining}/${q.quota_total || 100}` : 'n/a'}</strong>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button type="button" class="btn-sm" onclick="loadPublishDashboard()">🔄 Làm mới</button>
          <button type="button" class="btn-sm" onclick="startInstagramConnect()">🔁 Đổi tài khoản</button>
          <button type="button" class="btn-warn btn-sm" onclick="disconnectInstagramAccount()">🔌 Ngắt kết nối</button>
        </div>
      </div>`;
  }

  const igRows = (igStateCache.publishes || []).map(u => `
    <tr>
      <td class="mono" style="font-size:12px">
        <a href="${u.target_type === 'short' ? '#/shorts/' + esc(u.item_id) : '#/todos/' + esc(u.item_id) + '?tab=final_video'}" style="color:var(--accent)">${esc(u.item_id)}</a>
        ${u.target_type === 'short' ? '<span class="pill pill-accent" style="margin-left:4px;font-size:10px">Short</span>' : ''}
      </td>
      <td style="font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((u.caption || '').slice(0, 80))}</td>
      <td><span class="pill ${u.status === 'published' ? 'pill-ok' : (u.status === 'failed' || u.status === 'canceled' ? 'pill-warn' : 'pill-accent')}">${esc(u.status)}</span></td>
      <td style="font-size:12px">
        ${u.permalink ? `<a href="${esc(u.permalink)}" target="_blank" rel="noopener" style="color:var(--accent)">Mở Reels ↗</a>` : ''}
        ${u.publish_at ? `<div style="font-size:11px;color:var(--fg-muted)">🕒 Hẹn: ${esc(u.publish_at)}</div>` : ''}
        ${u.error_message ? `<div style="color:var(--danger);font-size:11px">${esc(u.error_message)}</div>` : ''}
      </td>
      <td>
        ${u.status === 'failed' ? `<button type="button" class="btn-sm" onclick="retryIgPublish('${esc(u.publish_id)}')">🔁 Thử lại</button>` : ''}
        ${['queued', 'creating', 'processing'].includes(u.status) ? `<button type="button" class="btn-warn btn-sm" onclick="cancelIgPublish('${esc(u.publish_id)}')">✖ Hủy</button>` : ''}
      </td>
    </tr>`).join('');

  const prodRows = (igStateCache.products || []).map(p => `
    <tr>
      <td style="font-weight:600">${esc(p.label)}</td>
      <td><span class="pill pill-ghost mono">${esc(p.network)}</span></td>
      <td class="mono" style="font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.cb_promoter || p.ebay_item_url || '')}</td>
      <td><span class="pill ${p.is_active ? 'pill-ok' : 'pill-warn'}">${p.is_active ? 'active' : 'off'}</span></td>
      <td><button type="button" class="btn-warn btn-sm" onclick="deleteAffiliateProduct('${esc(p.id)}')">🗑 Xóa</button></td>
    </tr>`).join('');

  const linkRows = (igStateCache.links || []).map(l => `
    <tr>
      <td class="mono" style="font-size:12px"><a href="${esc(l.url)}" target="_blank" rel="noopener" style="color:var(--accent)">/b/${esc(l.slug)}</a></td>
      <td><span class="pill pill-ghost mono">${esc(l.network)}</span></td>
      <td class="mono" style="font-size:12px">${esc(l.item_id || '')}</td>
      <td><strong>${Number(l.clicks || 0).toLocaleString('vi-VN')}</strong></td>
      <td class="mono" style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(l.deeplink)}</td>
    </tr>`).join('');

  const convRows = (igStateCache.conversions || []).map(c => `
    <tr>
      <td><span class="pill pill-ghost mono">${esc(c.network)}</span></td>
      <td style="font-size:12px">${esc(c.product_label || '')}</td>
      <td class="mono" style="font-size:12px">${esc(c.item_id || c.subid || '')}</td>
      <td><strong>$${Number(c.amount || 0).toFixed(2)}</strong></td>
      <td><span class="pill ${c.status === 'cleared' ? 'pill-ok' : 'pill-warn'}">${esc(c.status || '')}</span></td>
      <td class="mono" style="font-size:11px">${esc(c.event_date || '')}</td>
    </tr>`).join('');

  page.innerHTML = `
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">📲 Instagram Reels</h3></div>
      ${igCard}
    </section>
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">📤 Lịch sử đăng Instagram</h3></div>
      ${st.connected ? (igRows ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Job</th><th style="padding:8px">Caption</th><th style="padding:8px">Trạng thái</th><th style="padding:8px">Link</th><th style="padding:8px">Thao tác</th></tr></thead><tbody>${igRows}</tbody></table></div>` : '<div style="color:var(--fg-muted);font-size:13px">Chưa có lượt đăng Instagram nào.</div>') : ''}
    </section>
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">🛒 Affiliate — Sản phẩm (${(igStateCache.products || []).length})</h3>
        <div style="display:flex;gap:8px">
          <button type="button" class="btn-sm btn-primary" onclick="openAffiliateProductModal()">➕ Thêm sản phẩm</button>
          <button type="button" class="btn-sm" onclick="runAffiliateSync(this)">🔄 Sync doanh thu</button>
        </div>
      </div>
      ${prodRows ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Tên</th><th style="padding:8px">Mạng</th><th style="padding:8px">Chi tiết</th><th style="padding:8px">Trạng thái</th><th style="padding:8px">Thao tác</th></tr></thead><tbody>${prodRows}</tbody></table></div>` : '<div style="color:var(--fg-muted);font-size:13px">Chưa có sản phẩm affiliate. ClickBank cần CLICKBANK_NICKNAME, eBay cần EBAY_CAMPAIGN_ID trong .env.</div>'}
    </section>
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">🔗 Affiliate Links + Clicks</h3></div>
      ${linkRows ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Slug</th><th style="padding:8px">Mạng</th><th style="padding:8px">Job</th><th style="padding:8px">Clicks</th><th style="padding:8px">Deeplink</th></tr></thead><tbody>${linkRows}</tbody></table></div>` : '<div style="color:var(--fg-muted);font-size:13px">Gắn sản phẩm vào job khi bấm "📲 Đăng Instagram".</div>'}
    </section>
    <section class="panel">
      <div class="panel-header"><h3 class="panel-title">💰 Doanh thu (sync từ Reporting API)</h3></div>
      ${convRows ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Mạng</th><th style="padding:8px">Sản phẩm</th><th style="padding:8px">Job</th><th style="padding:8px">Hoa hồng</th><th style="padding:8px">Trạng thái</th><th style="padding:8px">Ngày</th></tr></thead><tbody>${convRows}</tbody></table></div>` : '<div style="color:var(--fg-muted);font-size:13px">Chưa có conversion nào. Cron tự sync mỗi 6h khi có API key.</div>'}
    </section>`;
}

async function startInstagramConnect() {
  try {
    const r = await apiFetch('/api/v1/instagram/auth/start');
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    window.location.href = d.auth_url;
  } catch (err) {
    showToast(`❌ Không thể mở Instagram OAuth: ${err.message}`, 'err', 5000);
  }
}

async function disconnectInstagramAccount() {
  if (!confirm('Ngắt kết nối tài khoản Instagram? Các publish đang chạy sẽ bị hủy.')) return;
  try {
    const r = await apiFetch('/api/v1/instagram/account', { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast('🔌 Đã ngắt kết nối Instagram', 'ok', 3000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi ngắt kết nối: ${err.message}`, 'err', 4500);
  }
}

async function retryIgPublish(publishId) {
  try {
    const r = await apiFetch(`/api/v1/instagram/publishes/${publishId}/retry`, { method: 'POST' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast('🔁 Đang thử lại đăng Instagram...', 'info', 3000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi retry: ${err.message}`, 'err', 4500);
  }
}

async function cancelIgPublish(publishId) {
  try {
    await apiFetch(`/api/v1/instagram/publishes/${publishId}/cancel`, { method: 'POST' });
    showToast('✖ Đã hủy publish Instagram', 'ok', 3000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi hủy: ${err.message}`, 'err', 4500);
  }
}

// -- Đăng Instagram từ todo/short ---------------------------------------------

async function openIgPublishModal(targetId, targetType = 'todo') {
  let products = [];
  try {
    const r = await apiFetch('/api/v1/affiliate/products');
    if (r.ok) products = (await r.json()).products || [];
  } catch (_) {}
  const activeProducts = products.filter(p => p.is_active);
  const old = document.getElementById('igModalOverlay');
  if (old) old.remove();
  const productOptions = activeProducts.map(p =>
    `<option value="${esc(p.id)}">${esc(p.label)} (${esc(p.network)})</option>`).join('');
  const wrap = document.createElement('div');
  wrap.id = 'igModalOverlay';
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
  wrap.innerHTML = `
    <div style="background:var(--card,#141821);border:1px solid var(--border);border-radius:12px;max-width:540px;width:100%;padding:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0;font-size:16px">${targetType === 'short' ? '📲 Đăng Short lên Instagram Reels' : '📲 Đăng Video lên Instagram Reels'}</h3>
        <button type="button" class="btn-sm" onclick="document.getElementById('igModalOverlay')?.remove()">✖</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">
        <label>Caption (hệ thống tự chèn #ad + "Link in bio" nếu chọn sản phẩm affiliate)
          <textarea id="igPubCaption" rows="4" maxlength="2200" placeholder="Hook dòng đầu — hiện trước nút 'more'..." style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)"></textarea>
        </label>
        <label>Sản phẩm affiliate (tùy chọn)
          <select id="igPubProduct" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
            <option value="">— Không gắn link —</option>
            ${productOptions}
          </select>
        </label>
        ${activeProducts.length === 0 ? '<div style="font-size:11px;color:var(--fg-muted)">💡 Chưa có sản phẩm affiliate — thêm ở tab 📲 Publish. Link sẽ tự sinh /b/{slug} + đo click.</div>' : ''}
        <label style="flex:1">Hẹn giờ đăng (tùy chọn)
          <input id="igPubAt" type="datetime-local" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
        </label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input id="igPubShareFeed" type="checkbox" checked> Đồng thời hiển thị trên Feed (share_to_feed)
        </label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input id="igPubAi" type="checkbox" checked> Khai báo nội dung AI-generated (khuyến nghị bật — đúng policy)
        </label>
        <div style="font-size:11px;color:var(--fg-muted)">
          💡 Reels API: MP4/MOV ≤300MB, 3s–15min, khuyến nghị 9:16. Giới hạn Meta: 100 bài/24h. Caption tự cắt ≤2.200 ký tự.
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">
          <button type="button" class="btn-sm" onclick="document.getElementById('igModalOverlay')?.remove()">Hủy</button>
          <button type="button" class="btn-primary btn-sm" onclick="submitIgPublish('${esc(targetId)}', this, '${esc(targetType)}')">🚀 Đăng Reels</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
}

async function submitIgPublish(targetId, btnEl, targetType = 'todo') {
  const caption = document.getElementById('igPubCaption')?.value || '';
  const productId = document.getElementById('igPubProduct')?.value || null;
  const pubAtRaw = document.getElementById('igPubAt')?.value || '';
  const publishAt = pubAtRaw ? new Date(pubAtRaw).toISOString() : null;
  const shareToFeed = !!document.getElementById('igPubShareFeed')?.checked;
  const isAi = !!document.getElementById('igPubAi')?.checked;

  const endpoint = targetType === 'short'
    ? `/api/v1/shorts/${encodeURIComponent(targetId)}/instagram/publish`
    : `/api/v1/todos/${encodeURIComponent(targetId)}/instagram/publish`;

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang gửi...'; }
  try {
    const r = await apiFetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        caption, affiliate_product_id: productId, publish_at: publishAt,
        share_to_feed: shareToFeed, is_ai_generated: isAi,
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    document.getElementById('igModalOverlay')?.remove();
    if (publishAt) {
      showToast('🕒 Đã lên lịch đăng Instagram đúng giờ hẹn.', 'ok', 4000);
    } else {
      showToast('🚀 Đang xử lý đăng Instagram Reels...', 'info', 4000);
      pollIgPublishProgress(targetId, d.publish_id, targetType);
    }
  } catch (err) {
    showToast(`❌ Lỗi đăng Instagram: ${err.message}`, 'err', 6000);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🚀 Đăng Reels'; }
  }
}

function pollIgPublishProgress(targetId, publishId, targetType = 'todo') {
  if (igPollTimers[publishId]) clearInterval(igPollTimers[publishId]);
  igPollTimers[publishId] = setInterval(async () => {
    try {
      const r = await apiFetch(`/api/v1/instagram/publishes/${publishId}`);
      if (!r.ok) return;
      const u = await r.json();
      if (!['queued', 'creating', 'processing', 'scheduled'].includes(u.status)) {
        clearInterval(igPollTimers[publishId]);
        delete igPollTimers[publishId];
        if (u.status === 'published') {
          showToast(`🎉 Đã đăng Instagram thành công! ${u.permalink || ''}`, 'ok', 6000);
        } else if (u.status === 'failed') {
          showToast(`❌ Đăng Instagram lỗi: ${u.error_message || ''}`, 'err', 7000);
        }
        if (document.getElementById('pagePublish') && !document.getElementById('pagePublish').classList.contains('hidden')) {
          loadPublishDashboard();
        }
      }
    } catch (_) {}
  }, 3000);
}

// -- Affiliate management ------------------------------------------------------

function openAffiliateProductModal() {
  const old = document.getElementById('affModalOverlay');
  if (old) old.remove();
  const wrap = document.createElement('div');
  wrap.id = 'affModalOverlay';
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
  wrap.innerHTML = `
    <div style="background:var(--card,#141821);border:1px solid var(--border);border-radius:12px;max-width:500px;width:100%;padding:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0;font-size:16px">➕ Thêm sản phẩm Affiliate</h3>
        <button type="button" class="btn-sm" onclick="document.getElementById('affModalOverlay')?.remove()">✖</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">
        <label>Tên hiển thị
          <input id="affLabel" type="text" maxlength="120" placeholder="Ví dụ: Anker Soundcore P20i" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
        </label>
        <label>Mạng affiliate
          <select id="affNetwork" onchange="document.getElementById('affCbRow').style.display = this.value==='clickbank' ? 'flex' : 'none'; document.getElementById('affEbayRow').style.display = this.value==='ebay' ? 'flex' : 'none'" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
            <option value="clickbank">ClickBank (digital, hoa hồng 30-75%)</option>
            <option value="ebay">eBay Partner Network (1-4%)</option>
          </select>
        </label>
        <label id="affCbRow" style="display:flex">Promoter/vendor nickname của ClickBank
          <input id="affCbPromoter" type="text" maxlength="64" placeholder="vendor nickname (không phải account của bạn)" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
        </label>
        <label id="affEbayRow" style="display:none">URL sản phẩm eBay
          <input id="affEbayUrl" type="text" placeholder="https://www.ebay.com/itm/123456789" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
        </label>
        <div style="font-size:11px;color:var(--fg-muted)">Link hoa hồng tự sinh theo .env: CLICKBANK_NICKNAME / EBAY_CAMPAIGN_ID. Subid = job_{item_id} để đo video nào ra đơn.</div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">
          <button type="button" class="btn-sm" onclick="document.getElementById('affModalOverlay')?.remove()">Hủy</button>
          <button type="button" class="btn-primary btn-sm" onclick="submitAffiliateProduct(this)">💾 Lưu</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
}

async function submitAffiliateProduct(btnEl) {
  const label = (document.getElementById('affLabel')?.value || '').trim();
  const network = document.getElementById('affNetwork')?.value || 'clickbank';
  const cbPromoter = (document.getElementById('affCbPromoter')?.value || '').trim();
  const ebayUrl = (document.getElementById('affEbayUrl')?.value || '').trim();
  if (!label) { showToast('⚠️ Nhập tên sản phẩm.', 'warn', 3000); return; }
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang lưu...'; }
  try {
    const r = await apiFetch('/api/v1/affiliate/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ network, label, cb_promoter: cbPromoter, ebay_item_url: ebayUrl }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    document.getElementById('affModalOverlay')?.remove();
    showToast('✅ Đã thêm sản phẩm affiliate!', 'ok', 3000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi thêm sản phẩm: ${err.message}`, 'err', 5500);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '💾 Lưu'; }
  }
}

async function deleteAffiliateProduct(productId) {
  if (!confirm('Xóa sản phẩm affiliate này? (Link đã sinh vẫn giữ nguyên)')) return;
  try {
    const r = await apiFetch(`/api/v1/affiliate/products/${productId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast('🗑 Đã xóa sản phẩm', 'ok', 3000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi xóa: ${err.message}`, 'err', 4500);
  }
}

async function runAffiliateSync(btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang sync...'; }
  try {
    const r = await apiFetch('/api/v1/affiliate/sync', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    const summary = (d.results || []).map(x => `${x.network}: ${x.new ?? 0} mới${x.error ? ` (${x.error})` : ''}${x.skipped ? ` (${x.skipped})` : ''}`).join(' · ');
    showToast(`🔄 Sync xong — ${summary || 'không có network nào'}`, 'ok', 6000);
    loadPublishDashboard();
  } catch (err) {
    showToast(`❌ Lỗi sync: ${err.message}`, 'err', 5000);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🔄 Sync doanh thu'; }
  }
}
