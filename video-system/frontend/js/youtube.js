// youtube.js — dashboard ken + publish youtube
let ytStateCache = { status: null, videos: [], uploads: [] };
let ytPollTimers = {};

function goYoutubePage() {
  setActiveNav('youtube');
  if ((window.location.hash || '') !== '#/youtube') {
    suppressHashSync = true;
    window.location.hash = '#/youtube';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  openYoutubePage();
}

function openYoutubePage() {
  setActiveNav('youtube');
  hideTodoDetailDom();
  hideShortsPage();
  hidePublishPage();
  hideChatPage();
  document.getElementById('pageTodoList')?.classList.add('hidden');
  const page = document.getElementById('pageYoutube');
  if (!page) return;
  page.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'auto' });

  const h = window.location.hash || '';
  if (h.includes('yt=connected')) {
    showToast('✅ Đã kết nối kênh YouTube thành công!', 'ok', 4000);
  } else if (h.includes('yt=error')) {
    const m = h.match(/reason=([^&]+)/);
    const reason = m ? decodeURIComponent(m[1]) : 'OAuth thất bại';
    showToast(`❌ Lỗi kết nối YouTube: ${reason}`, 'err', 6000);
  }
  loadYoutubeDashboard();
}

async function loadYoutubeDashboard() {
  const page = document.getElementById('pageYoutube');
  if (!page) return;
  page.innerHTML = `<section class="panel"><div style="padding:16px;color:var(--fg-muted)">⏳ Đang tải trạng thái kênh YouTube...</div></section>`;
  try {
    const rs = await apiFetch('/api/v1/youtube/status');
    const st = await rs.json();
    ytStateCache.status = st;
    if (st.connected) {
      const rv = await apiFetch('/api/v1/youtube/videos?limit=25');
      if (rv.ok) {
        const dv = await rv.json();
        ytStateCache.videos = dv.videos || [];
        ytStateCache.uploads = dv.uploads || [];
      }
    } else {
      ytStateCache.videos = [];
      ytStateCache.uploads = [];
    }
    renderYoutubeDashboard();
  } catch (err) {
    page.innerHTML = `<section class="panel"><div style="padding:16px;color:var(--danger)">❌ Lỗi tải YouTube dashboard: ${esc(err.message)}</div></section>`;
  }
}

function renderYoutubeDashboard() {
  const page = document.getElementById('pageYoutube');
  if (!page) return;
  const st = ytStateCache.status || {};
  const ch = st.channel;
  const quotaPct = Math.min(100, Math.round(((st.quota_used_today || 0) / (st.quota_limit || 10000)) * 100));

  let channelCard = '';
  if (!st.configured) {
    channelCard = `
      <div style="padding:14px;border-radius:10px;border:1px dashed rgba(245,158,11,.35);background:var(--run-soft)">
        <div style="font-weight:700;margin-bottom:6px;color:var(--run)">⚙️ Chưa cấu hình YouTube OAuth trên máy chủ</div>
        <div style="font-size:12px;color:var(--fg-muted);line-height:1.6">
          Vui lòng thêm <code>YOUTUBE_CLIENT_ID</code>, <code>YOUTUBE_CLIENT_SECRET</code> và <code>YOUTUBE_TOKEN_ENCRYPTION_KEY</code> vào <code>.env</code>.<br>
          Authorized redirect URI trên Google Cloud Console: <code>${esc(st.redirect_uri || '')}</code>
        </div>
      </div>`;
  } else if (!st.connected || !ch) {
    channelCard = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-weight:700;font-size:15px">Chưa liên kết kênh YouTube</div>
          <div style="font-size:12px;color:var(--fg-muted)">Bấm nút bên phải để đăng nhập Google và cấp quyền quản lý/đăng video lên kênh của bạn.</div>
        </div>
        <button type="button" class="btn-primary" onclick="startYoutubeConnect()">🔗 Kết nối Kênh YouTube</button>
      </div>`;
  } else {
    channelCard = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px">
          ${ch.thumbnail_url ? `<img src="${esc(ch.thumbnail_url)}" alt="" style="width:56px;height:56px;border-radius:50%;object-fit:cover;border:1px solid var(--border)">` : ''}
          <div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <strong style="font-size:16px">${esc(ch.title || 'Kênh YouTube')}</strong>
              ${ch.custom_url ? `<span class="pill pill-ghost mono">${esc(ch.custom_url)}</span>` : ''}
              <span class="pill pill-ok">✅ Đã kết nối</span>
            </div>
            <div style="font-size:12px;color:var(--fg-muted);margin-top:4px">
              Subscribers: <strong>${Number(ch.subscriber_count || 0).toLocaleString('vi-VN')}</strong> ·
              Tổng video: <strong>${Number(ch.video_count || 0).toLocaleString('vi-VN')}</strong> ·
              Quota hôm nay: <strong class="mono">${Number(st.quota_used_today || 0).toLocaleString('vi-VN')} / ${Number(st.quota_limit || 10000).toLocaleString('vi-VN')}</strong> (${quotaPct}%)
            </div>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button type="button" class="btn-sm" onclick="loadYoutubeDashboard()">🔄 Làm mới</button>
          <button type="button" class="btn-sm" onclick="startYoutubeConnect()">🔁 Đổi kênh</button>
          <button type="button" class="btn-warn btn-sm" onclick="disconnectYoutubeChannel()">🔌 Ngắt kết nối</button>
        </div>
      </div>`;
  }

  const videosRows = (ytStateCache.videos || []).map(v => `
    <tr>
      <td style="width:110px">
        ${v.thumbnail_url ? `<img src="${esc(v.thumbnail_url)}" alt="" style="width:96px;height:54px;object-fit:cover;border-radius:6px">` : ''}
      </td>
      <td>
        <a href="${esc(v.youtube_url)}" target="_blank" rel="noopener" style="font-weight:600;color:var(--accent)">${esc(v.title)}</a>
        <div style="font-size:11px;color:var(--fg-muted);margin-top:2px">ID: <code>${esc(v.video_id)}</code> · ${esc((v.published_at || '').slice(0, 10))}</div>
      </td>
      <td><span class="pill ${v.privacy_status === 'public' ? 'pill-ok' : 'pill-warn'}">${esc(v.privacy_status)}</span></td>
      <td class="mono" style="font-size:12px">${Number(v.view_count || 0).toLocaleString('vi-VN')} views · ${Number(v.like_count || 0).toLocaleString('vi-VN')} likes</td>
      <td>
        <button type="button" class="btn-sm" onclick="openYoutubeEditVideoModal('${esc(v.video_id)}')">✏️ Sửa</button>
      </td>
    </tr>
  `).join('');

  const uploadRows = (ytStateCache.uploads || []).map(u => {
    const isShort = u.target_type === 'short';
    const detailHref = isShort ? `#/shorts/${esc(u.item_id)}` : `#/todos/${esc(u.item_id)}?tab=final_video`;
    return `
    <tr>
      <td class="mono" style="font-size:12px">
        <a href="${detailHref}" style="color:var(--accent)">${esc(u.item_id)}</a>
        ${isShort ? '<span class="pill pill-accent" style="margin-left:4px;font-size:10px">Short 9:16</span>' : ''}
      </td>
      <td>${esc(u.title || '')}</td>
      <td><span class="pill ${u.upload_status === 'uploaded' ? 'pill-ok' : (u.upload_status === 'failed' || u.upload_status === 'canceled' ? 'pill-warn' : 'pill-accent')}">${esc(u.upload_status)} (${Number(u.progress_pct || 0).toFixed(0)}%)</span></td>
      <td style="font-size:12px">
        ${u.youtube_url ? `<a href="${esc(u.youtube_url)}" target="_blank" rel="noopener" style="color:var(--accent)">Mở YouTube ↗</a>` : ''}
        ${u.publish_at ? `<div style="font-size:11px;color:var(--fg-muted)">🕒 Hẹn: ${esc(u.publish_at)}</div>` : ''}
        ${u.error_message ? `<div style="color:var(--danger);font-size:11px">${esc(u.error_message)}</div>` : ''}
      </td>
      <td>
        ${u.upload_status === 'failed' ? `<button type="button" class="btn-sm" onclick="retryYoutubeUpload('${esc(u.upload_id)}')">🔁 Thử lại</button>` : ''}
        ${['queued','uploading','processing'].includes(u.upload_status) ? `<button type="button" class="btn-warn btn-sm" onclick="cancelYoutubeUpload('${esc(u.upload_id)}')">✖ Hủy</button>` : ''}
      </td>
    </tr>`;
  }).join('');

  page.innerHTML = `
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">📺 Kênh YouTube Liên Kết</h3></div>
      ${channelCard}
    </section>
    ${st.connected ? `
    <section class="panel" style="margin-bottom:14px">
      <div class="panel-header"><h3 class="panel-title">🎬 Video trên Kênh (${(ytStateCache.videos || []).length})</h3></div>
      ${videosRows ? `<div style="overflow-x:auto"><table class="yt-table" style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Ảnh</th><th style="padding:8px">Tiêu đề</th><th style="padding:8px">Chế độ</th><th style="padding:8px">Thống kê</th><th style="padding:8px">Thao tác</th></tr></thead><tbody>${videosRows}</tbody></table></div>` : `<div style="color:var(--fg-muted);font-size:13px">Chưa có video nào trên kênh.</div>`}
    </section>
    <section class="panel">
      <div class="panel-header"><h3 class="panel-title">📤 Lịch sử Publish từ Video Factory</h3></div>
      ${uploadRows ? `<div style="overflow-x:auto"><table class="yt-table" style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="text-align:left;border-bottom:1px solid var(--border)"><th style="padding:8px">Job ID</th><th style="padding:8px">Tiêu đề</th><th style="padding:8px">Trạng thái</th><th style="padding:8px">Link / Ghi chú</th><th style="padding:8px">Thao tác</th></tr></thead><tbody>${uploadRows}</tbody></table></div>` : `<div style="color:var(--fg-muted);font-size:13px">Chưa có lượt publish nào từ hệ thống.</div>`}
    </section>
    ` : ''}
  `;
}

async function startYoutubeConnect() {
  try {
    const r = await apiFetch('/api/v1/youtube/auth/start');
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    window.location.href = d.auth_url;
  } catch (err) {
    showToast(`❌ Không thể mở Google OAuth: ${err.message}`, 'err', 5000);
  }
}

async function disconnectYoutubeChannel() {
  if (!confirm('Bạn có chắc muốn ngắt kết nối kênh YouTube khỏi hệ thống?')) return;
  try {
    const r = await apiFetch('/api/v1/youtube/channel', { method: 'DELETE' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast('🔌 Đã ngắt kết nối kênh YouTube.', 'ok', 3500);
    loadYoutubeDashboard();
  } catch (err) {
    showToast(`❌ Lỗi ngắt kết nối: ${err.message}`, 'err', 4500);
  }
}

async function retryYoutubeUpload(uploadId) {
  try {
    const r = await apiFetch(`/api/v1/youtube/uploads/${uploadId}/retry`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    showToast('🔁 Đã khởi động lại quá trình upload YouTube.', 'ok', 3500);
    loadYoutubeDashboard();
  } catch (err) {
    showToast(`❌ Lỗi retry: ${err.message}`, 'err', 4500);
  }
}

async function cancelYoutubeUpload(uploadId) {
  try {
    const r = await apiFetch(`/api/v1/youtube/uploads/${uploadId}/cancel`, { method: 'POST' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    showToast('✖ Đã hủy upload.', 'ok', 3000);
    loadYoutubeDashboard();
  } catch (err) {
    showToast(`❌ Lỗi hủy upload: ${err.message}`, 'err', 4500);
  }
}

async function refreshSingleShort(shortId) {
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}`, { timeoutMs: 20000 });
    if (!r.ok) return;
    const fresh = await r.json();
    shortDetailsCache[shortId] = fresh;
    const idx = allShorts.findIndex(x => x.id === shortId);
    if (idx >= 0) allShorts[idx] = fresh;
    else allShorts.unshift(fresh);
    shortsLoaded = true;
    updateOverviewStats();
    rerenderSingleShortCard(shortId);
    const shortPage = document.getElementById('pageShortStudio');
    if (shortPage && !shortPage.classList.contains('hidden')) {
      if (activeDetailShortId === shortId) {
        renderShortDetailPage(fresh);
      } else if (!activeDetailShortId) {
        renderShortStudioShell();
      }
    }
  } catch (err) {
    console.warn('[refreshSingleShort]', err);
  }
}

async function refreshSingleTarget(targetId, targetType = 'todo') {
  if (targetType === 'short') {
    await refreshSingleShort(targetId);
  } else {
    await refreshSingleItem(targetId, false);
  }
}

function renderYoutubeActionButton(target, targetType = 'todo', extraClass = 'btn-primary btn-sm') {
  if (!target) return '';
  const id = targetType === 'short' ? target.id : target.item_id;
  const u = target.youtube_upload;
  if (u && u.upload_status === 'uploaded' && u.youtube_url) {
    return `<a href="${esc(u.youtube_url)}" target="_blank" rel="noopener" class="btn-ok btn-sm" style="text-decoration:none" onclick="event.stopPropagation()">▶ Xem trên YouTube ↗</a>`;
  }
  if (u && ['queued', 'uploading', 'processing'].includes(u.upload_status)) {
    return `<button type="button" class="${esc(extraClass)}" disabled onclick="event.stopPropagation()">⏳ Đang đăng YT (${Number(u.progress_pct || 0).toFixed(0)}%)</button>`;
  }
  if (targetType === 'short') {
    if (target.status !== 'ready' || !target.video_asset_id) {
      return `<button type="button" class="${esc(extraClass)}" disabled title="Short cần có MP4 9:16 sẵn sàng trước khi đăng YouTube" onclick="event.stopPropagation()">⏳ Chờ MP4 9:16</button>`;
    }
    const label = (u && u.upload_status === 'failed') ? '🔁 Đăng lại YouTube' : '📺 Đăng lên YouTube';
    return `<button type="button" class="${esc(extraClass)}" onclick="event.stopPropagation();openYoutubePublishModal('${esc(id)}', 'short')">${label}</button>`;
  }
  const hasThumb = (target.uploaded_images || []).some(img => img.kind === 'thumbnail');
  const label = (u && u.upload_status === 'failed') ? '🔁 Đăng lại YouTube' : '📺 Đăng lên YouTube';
  return `<button type="button" class="${esc(extraClass)}" onclick="event.stopPropagation();openYoutubePublishModal('${esc(id)}', 'todo')" ${!hasThumb ? 'disabled title="Cần có 1 ảnh thumbnail trước khi đăng YouTube"' : ''}>${label}</button>`;
}

function formatYoutubeScheduleNote(publishAt) {
  if (!publishAt) return '';
  try {
    const dt = new Date(publishAt);
    if (!Number.isNaN(dt.getTime())) {
      return ` · 🕒 Hẹn công chiếu: ${dt.toLocaleString('vi-VN')}`;
    }
  } catch (_) {}
  return ` · 🕒 Hẹn: ${publishAt}`;
}

function renderTodoYoutubeStatusBanner(item, targetType = 'todo') {
  const u = item && item.youtube_upload;
  if (!u) return '';
  const targetId = targetType === 'short' ? (item.id || item.item_id) : (item.item_id || item.id);
  const schedNote = formatYoutubeScheduleNote(u.publish_at);
  if (u.upload_status === 'uploaded' && u.youtube_url) {
    return `<div style="margin:8px 0;padding:8px 10px;border-radius:8px;border:1px solid rgba(34,197,94,.35);background:var(--ok-soft);display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:12px">
      <span>✅ <strong>Đã đăng YouTube</strong> (${esc(u.privacy_status)})${esc(schedNote)} ${u.error_message ? `<span style="color:var(--warn)">· ${esc(u.error_message)}</span>` : ''}</span>
      <a href="${esc(u.youtube_url)}" target="_blank" rel="noopener" class="btn-xs btn-primary" style="text-decoration:none" onclick="event.stopPropagation()">▶ Xem trên YouTube ↗</a>
    </div>`;
  }
  if (['queued', 'uploading', 'processing'].includes(u.upload_status)) {
    return `<div id="ytUploadProgress-${esc(targetType)}-${esc(targetId)}" style="margin:8px 0;padding:8px 10px;border-radius:8px;border:1px solid rgba(45,212,191,.35);background:var(--accent-soft)">
      <div style="font-size:12px;font-weight:600">⏳ Đang đăng lên YouTube (${esc(u.upload_status)}): <span class="mono">${Number(u.progress_pct || 0).toFixed(1)}%</span></div>
      <div style="height:6px;border-radius:4px;background:rgba(255,255,255,.08);margin-top:6px;overflow:hidden"><div style="height:100%;width:${Math.min(100, Number(u.progress_pct || 0))}%;background:var(--accent)"></div></div>
    </div>`;
  }
  if (u.upload_status === 'failed') {
    return `<div style="margin:8px 0;padding:8px 10px;border-radius:8px;border:1px solid rgba(239,68,68,.35);background:var(--held-soft);display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="font-size:12px">❌ Đăng YouTube thất bại: ${esc(u.error_message || '')}</span>
      <button type="button" class="btn-xs" onclick="event.stopPropagation();retryYoutubeUpload('${esc(u.id || u.upload_id)}').then(() => refreshSingleTarget('${esc(targetId)}', '${esc(targetType)}'))">🔁 Thử lại</button>
    </div>`;
  }
  return '';
}

function buildShortPublishPrefill(s) {
  const sc = s.script || {};
  const ytTags = Array.isArray(sc.hashtags?.youtube) ? sc.hashtags.youtube : [];
  const normHashtags = ytTags.map(t => (String(t).startsWith('#') ? String(t) : `#${t}`));
  if (!normHashtags.some(h => h.toLowerCase() === '#shorts')) {
    normHashtags.unshift('#Shorts');
  }
  const descParts = [
    (sc.description || '').trim(),
    sc.source_note ? `Nguồn: ${sc.source_note}` : '',
    normHashtags.join(' '),
  ].filter(Boolean);
  const tagList = ['Shorts', ...normHashtags.map(h => h.replace(/^#/, '')).filter(Boolean)];
  const uniqueTags = Array.from(new Set(tagList));
  return {
    title: (s.title || '').slice(0, 100),
    description: descParts.join('\n\n'),
    tags: uniqueTags.join(', '),
    hasSrt: !!s.srt_asset_id,
  };
}

function openYoutubePublishModal(targetId, targetType = 'todo') {
  const target = targetType === 'short'
    ? (shortDetailsCache[targetId] || allShorts.find(s => s.id === targetId))
    : (detailsCache[targetId] || allTodos.find(t => t.item_id === targetId));
  if (!target) {
    showToast('Không tìm thấy sản phẩm để đăng YouTube.', 'error', 3000);
    return;
  }
  const isShort = targetType === 'short';
  const prefill = isShort
    ? buildShortPublishPrefill(target)
    : { title: (target.title || '').slice(0, 100), description: '', tags: '', hasSrt: true };
  const old = document.getElementById('ytModalOverlay');
  if (old) old.remove();
  const wrap = document.createElement('div');
  wrap.id = 'ytModalOverlay';
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
  wrap.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;max-width:540px;width:100%;padding:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0;font-size:16px">${isShort ? '📱 Đăng Short 9:16 lên YouTube' : '📺 Đăng Video lên YouTube'}</h3>
        <button type="button" class="btn-sm" onclick="document.getElementById('ytModalOverlay')?.remove()">✖</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">
        <label>Tiêu đề (tối đa 100 ký tự)<input id="ytPubTitle" type="text" maxlength="100" value="${esc(prefill.title)}" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)"></label>
        <label>Mô tả<textarea id="ytPubDesc" rows="3" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">${esc(prefill.description)}</textarea></label>
        <label>Tags (cách nhau dấu phẩy)<input id="ytPubTags" type="text" value="${esc(prefill.tags)}" placeholder="Shorts, finance, explainer" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)"></label>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <label style="flex:1">Chế độ hiển thị
            <select id="ytPubPrivacy" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
              <option value="private" selected>Private (an toàn mặc định)</option>
              <option value="unlisted">Unlisted (không công khai)</option>
              <option value="public">Public (công khai)</option>
            </select>
          </label>
          <label style="flex:1">Hẹn giờ đăng (tùy chọn, ≥10 phút)
            <input id="ytPubAt" type="datetime-local" onchange="document.getElementById('ytPubPrivacy').disabled = !!this.value; if(this.value) document.getElementById('ytPubPrivacy').value='private';" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
          </label>
        </div>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input id="ytPubSrt" type="checkbox" ${prefill.hasSrt ? 'checked' : 'disabled'}> ${isShort && !prefill.hasSrt ? 'Short này chưa có file phụ đề .srt' : 'Đính kèm phụ đề (.srt) nếu có'}
        </label>
        <div style="font-size:11px;color:var(--fg-muted)">
          ${isShort
            ? `💡 Video Short 9:16 (${Number(target.duration_s || 0).toFixed(1)}s) sẽ được upload trực tiếp lên YouTube Shorts. Để trống "Hẹn giờ" để đăng ngay, hoặc chọn giờ (tối thiểu +10 phút).`
            : `💡 Lưu ý: Hệ thống tự đính kèm Video thành phẩm + Ảnh Thumbnail đã duyệt. Nếu OAuth app chưa verify với Google, video mới sẽ bị giữ ở chế độ Private.`}
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">
          <button type="button" class="btn-sm" onclick="document.getElementById('ytModalOverlay')?.remove()">Hủy</button>
          <button type="button" class="btn-primary btn-sm" onclick="submitYoutubePublish('${esc(targetId)}', this, '${esc(targetType)}')">🚀 Bắt đầu Đăng</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
}

async function submitYoutubePublish(targetId, btnEl, targetType = 'todo') {
  const title = (document.getElementById('ytPubTitle')?.value || '').trim();
  if (!title) { showToast('⚠️ Vui lòng nhập tiêu đề video.', 'warn', 3000); return; }
  const description = document.getElementById('ytPubDesc')?.value || '';
  const tagsRaw = document.getElementById('ytPubTags')?.value || '';
  const tags = tagsRaw.split(',').map(s => s.trim()).filter(Boolean);
  const privacy = document.getElementById('ytPubPrivacy')?.value || 'private';
  const pubAtRaw = document.getElementById('ytPubAt')?.value || '';
  const publishAt = pubAtRaw ? new Date(pubAtRaw).toISOString() : null;
  const uploadCaptions = !!document.getElementById('ytPubSrt')?.checked;

  const endpoint = targetType === 'short'
    ? `/api/v1/shorts/${encodeURIComponent(targetId)}/youtube/publish`
    : `/api/v1/todos/${encodeURIComponent(targetId)}/youtube/publish`;

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang gửi...'; }
  try {
    const r = await apiFetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title, description, tags, privacy_status: privacy, publish_at: publishAt, upload_captions: uploadCaptions
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    document.getElementById('ytModalOverlay')?.remove();
    if (d.already_published) {
      showToast(`✅ Video đã được đăng trước đó: ${d.youtube_url}`, 'ok', 4500);
    } else {
      showToast(publishAt ? '🕒 Đã lên lịch & đang upload video lên YouTube...' : '🚀 Đang upload video lên YouTube...', 'info', 4000);
      pollYoutubeUploadProgress(targetId, d.upload_id, targetType);
    }
    await refreshSingleTarget(targetId, targetType);
  } catch (err) {
    showToast(`❌ Lỗi đăng YouTube: ${err.message}`, 'err', 5500);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '🚀 Bắt đầu Đăng'; }
  }
}

function pollYoutubeUploadProgress(targetId, uploadId, targetType = 'todo') {
  if (ytPollTimers[uploadId]) clearInterval(ytPollTimers[uploadId]);
  ytPollTimers[uploadId] = setInterval(async () => {
    try {
      const r = await apiFetch(`/api/v1/youtube/uploads/${uploadId}`);
      if (!r.ok) return;
      const u = await r.json();
      if (!['queued', 'uploading', 'processing'].includes(u.upload_status)) {
        clearInterval(ytPollTimers[uploadId]);
        delete ytPollTimers[uploadId];
        if (u.upload_status === 'uploaded') {
          showToast(`🎉 Đã đăng YouTube thành công!`, 'ok', 5000);
        } else if (u.upload_status === 'failed') {
          showToast(`❌ Upload YouTube lỗi: ${u.error_message || ''}`, 'err', 6000);
        }
        await refreshSingleTarget(targetId, targetType);
      } else {
        const box = document.getElementById(`ytUploadProgress-${targetType}-${targetId}`)
          || document.getElementById(`ytUploadProgress-${targetId}`);
        if (box) {
          box.querySelector('.mono').textContent = `${Number(u.progress_pct || 0).toFixed(1)}%`;
        }
      }
    } catch (_) {}
  }, 2000);
}

function openYoutubeEditVideoModal(videoId) {
  const v = (ytStateCache.videos || []).find(x => x.video_id === videoId);
  if (!v) return;
  const old = document.getElementById('ytModalOverlay');
  if (old) old.remove();
  const wrap = document.createElement('div');
  wrap.id = 'ytModalOverlay';
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
  wrap.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;max-width:520px;width:100%;padding:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <h3 style="margin:0;font-size:16px">✏️ Sửa Video YouTube (${esc(videoId)})</h3>
        <button type="button" class="btn-sm" onclick="document.getElementById('ytModalOverlay')?.remove()">✖</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">
        <label>Tiêu đề<input id="ytEditTitle" type="text" maxlength="100" value="${esc(v.title || '')}" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)"></label>
        <label>Mô tả<textarea id="ytEditDesc" rows="4" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">${esc(v.description || '')}</textarea></label>
        <label>Tags (cách nhau dấu phẩy)<input id="ytEditTags" type="text" value="${esc((v.tags || []).join(', '))}" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)"></label>
        <label>Chế độ hiển thị
          <select id="ytEditPrivacy" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg)">
            <option value="private" ${v.privacy_status === 'private' ? 'selected' : ''}>Private</option>
            <option value="unlisted" ${v.privacy_status === 'unlisted' ? 'selected' : ''}>Unlisted</option>
            <option value="public" ${v.privacy_status === 'public' ? 'selected' : ''}>Public</option>
          </select>
        </label>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:6px">
          <button type="button" class="btn-sm" onclick="document.getElementById('ytModalOverlay')?.remove()">Hủy</button>
          <button type="button" class="btn-primary btn-sm" onclick="submitYoutubeEditVideo('${esc(videoId)}', this)">💾 Lưu thay đổi</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(wrap);
}

async function submitYoutubeEditVideo(videoId, btnEl) {
  const title = (document.getElementById('ytEditTitle')?.value || '').trim();
  const description = document.getElementById('ytEditDesc')?.value || '';
  const tags = (document.getElementById('ytEditTags')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
  const privacy_status = document.getElementById('ytEditPrivacy')?.value || 'private';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang lưu...'; }
  try {
    const r = await apiFetch(`/api/v1/youtube/videos/${videoId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, description, tags, privacy_status })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || d.detail || `HTTP ${r.status}`);
    document.getElementById('ytModalOverlay')?.remove();
    showToast('✅ Đã cập nhật video trên YouTube!', 'ok', 3500);
    loadYoutubeDashboard();
  } catch (err) {
    showToast(`❌ Lỗi cập nhật video: ${err.message}`, 'err', 5000);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = '💾 Lưu thay đổi'; }
  }
}

// =====================================================================
// PUBLISH ĐA NỀN TẢNG (#/publish) — Instagram Reels + Affiliate
// =====================================================================
