// shorts.js — short studio + news inbox + muse proposals inbox
let MP_CACHE = null;

function mpIsOwner() {
  return !!(VS_ME && (VS_ME.scope === 'admin' || VS_ME.identity === 'owner'));
}

async function mpLoadInbox() {
  const el = document.getElementById('mpInboxList');
  if (!el) return;
  try {
    const r = await apiFetch('/api/v1/muse-proposals', { timeoutMs: 20000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không tải được inbox đề xuất'));
    MP_CACHE = await r.json();
    mpPaint();
  } catch (err) {
    el.innerHTML = `<div class="rb-empty">❌ ${esc(err.message)}</div>`;
  }
}

function mpPaint() {
  const el = document.getElementById('mpInboxList');
  if (!el || !MP_CACHE) return;
  el.innerHTML = mpRenderInbox(MP_CACHE.proposals || []);
  const props = MP_CACHE.proposals || [];
  const awaiting = props.filter(p => p.status === 'awaiting_owner').length;
  const reviewing = props.filter(p => p.status === 'under_review' || p.status === 'proposed').length;
  const bA = document.getElementById('mpBadgeAwaiting');
  const bR = document.getElementById('mpBadgeReviewing');
  if (bA) { bA.textContent = `Chờ duyệt: ${awaiting}`; bA.classList.toggle('hidden', awaiting === 0); }
  if (bR) { bR.textContent = `Đang chấm: ${reviewing}`; bR.classList.toggle('hidden', reviewing === 0); }
}

function mpPriorityChip(p) {
  const label = { 1: 'P1 gấp', 2: 'P2 thường', 3: 'P3 thấp' }[p] || `P${p}`;
  const cls = p === 1 ? 'pill-danger' : (p === 2 ? 'pill-warn' : 'pill-ghost');
  return `<span class="pill ${cls}" style="font-size:10px;padding:1px 7px">${label}</span>`;
}

function mpStatusPill(p) {
  const map = {
    proposed: ['pill-ghost', 'Mới đề xuất'],
    under_review: ['pill-warn', 'Đang chấm'],
    awaiting_owner: ['pill-accent', 'Chờ duyệt'],
    needs_script: ['pill-ok', 'Cần kịch bản'],
    in_production: ['pill-ok', 'Đang sản xuất'],
    rejected: ['pill-danger', 'Đã từ chối'],
    expired: ['pill-ghost', 'Quá hạn'],
  };
  const [cls, label] = map[p.status] || ['pill-ghost', p.status];
  return `<span class="pill ${cls}" style="font-size:10px;padding:1px 7px">${label}</span>`;
}

function mpCanConvert() {
  return !!(VS_ME && VS_ME.scope && VS_ME.scope !== 'read');
}

function mpPropCard(p, opts = {}) {
  const reviewsHtml = (p.reviews || []).map(r => {
    const scorePart = r.score != null ? `${r.score} · ${String(r.verdict || '').toUpperCase()}` : '· ẩn (chưa chấm)';
    const cmtPart = r.comment ? ` — ${esc(String(r.comment).slice(0, 140))}` : '';
    return `<div class="cmt-meta" style="margin-left:2px">• <b>${esc(r.reviewer)}</b>: ${scorePart}${cmtPart}</div>`;
  }).join('');
  const progress = `${p.review_count}/${p.reviews_required || 3} lượt chấm`;
  const briefLine = p.brief_id
    ? `<div class="rb-ded" style="margin-top:4px">🧾 Brief: <span class="mono">${esc(p.brief_id)}</span> — kịch bản đang được viết qua luồng brief hiện có</div>` : '';
  const convertBtn = p.status === 'needs_script' && mpCanConvert() ? `
    <div style="margin-top:8px">
      <button type="button" class="btn-primary btn-sm" onclick="mpConvertToBrief('${esc(p.id)}', this)">🎬 Chuyển sang brief (gen kịch bản)</button>
      <span class="rb-ded">— script-writer lấy đề xuất ở đây để tạo brief viết kịch bản</span>
    </div>` : '';
  const ownerBtns = mpIsOwner() && p.status === 'awaiting_owner' ? `
    <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
      <button type="button" class="btn-ok btn-sm" onclick="mpApprove('${esc(p.id)}', this)">✅ Duyệt → Cần kịch bản</button>
      <button type="button" class="btn-sm btn-danger" onclick="mpReject('${esc(p.id)}', this)">❌ Từ chối</button>
      <select id="mp-prio-${esc(p.id)}" title="Priority khi duyệt" style="width:130px;font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:8px">
        <option value="">Priority tự động</option>
        <option value="1">P1 gấp</option>
        <option value="2">P2 thường</option>
        <option value="3">P3 thấp</option>
      </select>
    </div>` : '';
  return `
  <div class="rb-workcard" id="mp-card-${esc(p.id)}" style="margin-bottom:10px">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:4px">
      ${mpStatusPill(p)}
      ${p.status === 'needs_script' ? mpPriorityChip(p.priority) : ''}
      <span class="rb-ded mono">${esc(p.id)}</span>
      <span class="rb-ded">bởi ${esc(p.proposer)}${p.signals > 1 ? ` · ${p.signals} muse cùng đề xuất` : ''}</span>
    </div>
    <div style="font-weight:700;font-size:13.5px;margin-bottom:3px">${esc(p.title)}</div>
    <div style="font-size:12.5px;margin-bottom:4px">🎣 ${esc(p.hook)}</div>
    ${p.angle ? `<div style="font-size:12px;color:var(--fg-muted);margin-bottom:4px">Góc nhìn: ${esc(p.angle)}</div>` : ''}
    ${p.source_url ? `<div class="rb-ded">🔗 <a href="${esc(p.source_url)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(p.source_url)}</a></div>` : ''}
    <div class="rb-ded" style="margin-top:4px">${p.mode}/${p.lang} · ~${Math.round(p.target_seconds)}s · ${progress}${p.avg_score != null ? ` · avg ${p.avg_score}` : ''}</div>
    ${reviewsHtml ? `<div style="margin-top:6px">${reviewsHtml}</div>` : ''}
    ${!p.reviews_visible && (p.review_count || 0) > 0 ? '<div class="rb-ded">🔒 Điểm reviewer ẩn cho tới khi bạn chấm đề xuất này</div>' : ''}
    ${briefLine}
    ${convertBtn}
    ${ownerBtns}
  </div>`;
}

function mpRenderInbox(props) {
  const by = st => props.filter(p => p.status === st);
  const awaiting = by('awaiting_owner');
  const reviewing = [...by('under_review'), ...by('proposed')];
  const needs = [...by('needs_script')].sort((a, b) => a.priority - b.priority || a.created_at - b.created_at);
  const producing = by('in_production');
  const done = [...by('rejected'), ...by('expired')];
  // B3: đề xuất của chính tôi — proposer phải đọc được nhận xét reviewer khi
  // proposal đã chốt (reviews mở sau chốt) — vòng học tập không bị đứt
  const mine = (VS_ME && VS_ME.identity)
    ? props.filter(p => p.proposer === VS_ME.identity)
    : [];

  const sec = (title, list, extra = '') => `
    <div style="font-weight:700;font-size:12.5px;margin:12px 0 6px">${title}${list.length ? ` (${list.length})` : ''}</div>
    ${list.length ? list.map(p => mpPropCard(p, extra)).join('') : '<div class="cmt-meta">— trống —</div>'}`;

  return `
    ${sec('⏳ Chờ bạn duyệt (≥2/3 pass ≥9.5)', awaiting)}
    ${sec('🧪 Đang chấm bởi các reviewer', reviewing)}
    ${sec('📋 Cần kịch bản — hàng chờ ưu tiên gen kịch bản', needs)}
    ${sec('🎬 Đang sản xuất — brief đã tạo, kịch bản đang viết', producing)}
    ${sec('📨 Đề xuất của tôi — kết quả & góp ý của reviewer', mine)}
    <details style="margin-top:10px">
      <summary style="font-weight:700;font-size:12.5px;cursor:pointer">🗑 Đã từ chối / Quá hạn${done.length ? ` (${done.length})` : ''}</summary>
      <div style="margin-top:6px">${done.length ? done.map(p => mpPropCard(p)).join('') : '<div class="cmt-meta">— trống —</div>'}</div>
    </details>`;
}

// ---- Reviewer chấm đề xuất (4 trục + tổng + comment) ----
async function mpTakeReviewJob(btn) {
  const area = document.getElementById('mpWorkArea');
  if (!area) return;
  if (btn) btn.disabled = true;
  area.innerHTML = '<div class="rb-empty"><span class="spinner-ring"></span> Đang xin đề xuất để chấm...</div>';
  try {
    const r = await apiFetch('/api/v1/muse-proposals/next?mode=review', { timeoutMs: 15000 });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { area.innerHTML = rbErrorHtml(d.detail, d.code); return; }
    if (!d.proposal) {
      area.innerHTML = `<div class="rb-empty">🎉 Hết đề xuất phù hợp với bạn lúc này (${esc(d.reason || 'no_review_proposal')}).${d.retry_after_sec ? ` Thử lại sau ~${d.retry_after_sec}s.` : ''}</div>`;
      return;
    }
    mpRenderReviewForm(d.proposal);
  } catch (err) {
    area.innerHTML = rbErrorHtml(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function mpRenderReviewForm(p) {
  const area = document.getElementById('mpWorkArea');
  if (!area) return;
  const dims = [
    ['hook', 'Hook — câu mở giữ chân người xem', 3],
    ['topicality', 'Tính thời sự — đang nóng, đáng nói bây giờ', 3],
    ['freshness', 'Độ mới — chưa trùng nội dung đã làm', 2],
    ['feasibility', 'Khả thi sản xuất trong ngày (ảnh/B-roll có sẵn)', 2],
  ];
  area.innerHTML = `
  <div class="rb-workcard" id="mp-review-form" style="border-color:var(--accent)">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
      <span class="pill pill-accent" style="font-size:10px;padding:1px 7px">CHẤM ĐỀ XUẤT</span>
      <span class="rb-ded mono">${esc(p.id)}</span>
      <button type="button" class="btn-xs" onclick="mpTakeReviewJob()" title="Bỏ qua, lấy đề xuất khác">⏭ Bỏ qua</button>
    </div>
    <div style="font-weight:700;font-size:14px;margin-bottom:4px">${esc(p.title)}</div>
    <div style="font-size:12.5px;margin-bottom:6px">🎣 ${esc(p.hook)}</div>
    ${p.angle ? `<div style="font-size:12px;color:var(--fg-muted);margin-bottom:6px">Góc nhìn: ${esc(p.angle)}</div>` : ''}
    ${p.source_url ? `<div class="rb-ded" style="margin-bottom:8px">🔗 <a href="${esc(p.source_url)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(p.source_url)}</a> ${p.source_note ? '· ' + esc(p.source_note) : ''}</div>` : ''}
    <div class="rb-defect-grid">
      ${dims.map(([k, label, max]) => `
        <div class="rb-defect-item" style="cursor:default">
          <span style="flex:1">${esc(label)}</span>
          <input type="number" class="mp-dim" data-max="${max}" min="0" max="${max}" step="0.1" value="${max}"
                 id="mp-dim-${k}" onchange="mpSyncScore()" style="width:64px;padding:4px 6px;border:1px solid var(--border);border-radius:8px;font:inherit;font-size:12.5px"/>
          <span class="rb-ded">/${max}</span>
        </div>`).join('')}
    </div>
    <div class="rb-preview" id="mp-score-preview"></div>
    <input type="text" id="mp-comment" placeholder="Góp ý cho người đề xuất (không bắt buộc)" style="width:100%;margin-top:8px;padding:7px 9px;border:1px solid var(--border);border-radius:8px;font:inherit;font-size:12.5px"/>
    <button type="button" class="btn-primary btn-sm" id="mp-submit-btn" style="width:100%;margin-top:8px" onclick="mpSubmitReview('${esc(p.id)}')">✅ Gửi chấm (server tự tính pass ≥9.5)</button>
  </div>`;
  mpSyncScore();
}

function mpSyncScore() {
  const inputs = Array.from(document.querySelectorAll('#mp-review-form .mp-dim'));
  let total = 0;
  for (const inp of inputs) {
    let v = parseFloat(inp.value || '0');
    if (isNaN(v) || v < 0) v = 0;
    const max = parseFloat(inp.dataset.max || '3');
    if (v > max) { v = max; inp.value = max; }
    total += v;
  }
  total = Math.round(total * 10) / 10;
  const preview = document.getElementById('mp-score-preview');
  if (preview) {
    const verdict = total >= 9.5 ? 'PASS' : 'FAIL';
    preview.innerHTML = `Tổng điểm dự kiến: <span class="pill ${verdict === 'PASS' ? 'pill-ok' : 'pill-danger'}" style="font-size:11px;padding:1px 8px">${total.toFixed(1)} · ${verdict}</span>
      <span class="rb-ded">— ngưỡng pass 9.5; server vẫn tự tính lại</span>`;
  }
}

async function mpSubmitReview(pid) {
  const inputs = Array.from(document.querySelectorAll('#mp-review-form .mp-dim'));
  const dimensions = {};
  for (const inp of inputs) dimensions[inp.id.replace('mp-dim-', '')] = parseFloat(inp.value || '0');
  const btn = document.getElementById('mp-submit-btn');
  const commentEl = document.getElementById('mp-comment');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang gửi chấm...'; }
  try {
    const r = await apiFetch(`/api/v1/muse-proposals/${pid}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : `mp-${Date.now()}-${Math.random().toString(36).slice(2)}`) },
      // Q3: KHÔNG gửi score — server tự tính từ tổng 4 trục (client score bị chặn)
      body: JSON.stringify({ dimensions, comment: commentEl ? (commentEl.value.trim() || undefined) : undefined }),
      timeoutMs: 20000
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    const st = d.status || '';
    showToast(st === 'awaiting_owner' ? '✅ Đủ 2/3 pass — đã gửi Telegram chờ owner duyệt'
      : (st === 'rejected' ? '❌ Đề xuất bị loại (không đủ 2/3 pass)' : '✅ Đã chấm — chờ reviewer khác'), st === 'rejected' ? 'info' : 'ok', 5000);
    await mpLoadInbox();
    await mpTakeReviewJob();
  } catch (err) {
    showToast(`❌ Gửi chấm thất bại: ${err.message || err}`, 'error', 6000);
    if (btn) { btn.disabled = false; btn.textContent = '✅ Gửi chấm (server tự tính pass ≥9.5)'; }
  }
}

// ---- Script-writer tiêu thụ hàng chờ (Q6) ----
async function mpConvertToBrief(pid, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang tạo brief...'; }
  try {
    const r = await apiFetch(`/api/v1/muse-proposals/${pid}/convert-to-brief`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : `mpc-${Date.now()}-${Math.random().toString(36).slice(2)}`) },
      body: JSON.stringify({}),
      timeoutMs: 20000
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    showToast(`🎬 Đã tạo brief ${d.brief_id} — kịch bản vào luồng viết script`, 'ok', 6000);
    await mpLoadInbox();
  } catch (err) {
    showToast(`❌ ${err.message || 'Tạo brief thất bại'}`, 'error', 6000);
    if (btn) { btn.disabled = false; btn.textContent = '🎬 Chuyển sang brief (gen kịch bản)'; }
  }
}

// ---- Owner duyệt / từ chối ----
async function mpApprove(pid, btn) {
  const prioEl = document.getElementById(`mp-prio-${pid}`);
  const prio = prioEl && prioEl.value ? parseInt(prioEl.value, 10) : null;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang duyệt...'; }
  try {
    const r = await apiFetch(`/api/v1/muse-proposals/${pid}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(prio ? { priority: prio } : {}),
      timeoutMs: 15000
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Duyệt thất bại'));
    const d = await r.json();
    showToast(`✅ Đã duyệt → needs_script (priority ${d.priority})`, 'ok', 5000);
    await mpLoadInbox();
  } catch (err) {
    showToast(`❌ ${err.message || 'Duyệt thất bại'}`, 'error', 6000);
    if (btn) { btn.disabled = false; btn.textContent = '✅ Duyệt → Cần kịch bản'; }
  }
}

async function mpReject(pid, btn) {
  const reason = prompt('Lý do từ chối (không bắt buộc):');
  if (reason === null) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang từ chối...'; }
  try {
    const r = await apiFetch(`/api/v1/muse-proposals/${pid}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason || undefined }),
      timeoutMs: 15000
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Từ chối thất bại'));
    showToast('🗑 Đã từ chối đề xuất', 'ok', 4000);
    await mpLoadInbox();
  } catch (err) {
    showToast(`❌ ${err.message || 'Từ chối thất bại'}`, 'error', 6000);
    if (btn) { btn.disabled = false; btn.textContent = '❌ Từ chối'; }
  }
}

// --- Interactive Shot Checklist Board for Video Generation Agent ---
let allShorts = [];
let shortsLoaded = false;
let shortDetailsCache = {};
let shortNewsList = [];
let shortMode = 'news';
let shortLang = 'vi';          // vi | en — mảng quốc tế dùng giọng + prompt EN
let newsLangFilter = '';       // '' = tất cả, 'vi', 'en'
let shortCreateTab = 'ai';     // 'ai' | 'upload' | 'audio'
let shortUploadFileObj = null;
let shortUploadPreviewUrl = '';
let shortQuickPreviewId = '';
// Tab "Nạp audio + shot" — audio owner (không TTS), timing do người dùng khai
let shortAudioFileObj = null;
let shortAudioTitleText = '';
let shortAudioShotsText = '';
let shortAudioMode = 'cinematic';
let shortAudioLang = 'vi';
let shortAudioCreatedId = '';
const SHORT_MODE_META = {
  news: { label: 'Tin nóng', hint: '25–40s · 80–110 từ · hook giật gân → 3 beat → câu hỏi mở', target: 32 },
  decode: { label: 'Giải mã tiền bạc', hint: '40–60s · 110–160 từ · nghịch lý → cơ chế → chốt loop', target: 48 },
  daily_digest: { label: 'Daily World Brief (EN)', hint: '140–170s · 250–430 từ · 12–15 tin quốc tế (<180s Shorts)', target: 155 },
  trailer: { label: 'Trailer video dài (EN)', hint: '30–45s · 70–105 từ EN · hook định vị → 2-3 beat tease (không spoil) → CTA về video dài', target: 38 },
  cinematic: { label: 'Kể chuyện điện ảnh', hint: '30–60s · 100–140 từ · kể chuyện, không badge tin tức', target: 45 }
};
const SHORT_STATUS_PILL = {
  draft: 'pill-ghost', scripted: 'pill-ready', queued: 'pill-warn',
  rendering: 'pill-warn', producing: 'pill-warn',
  ready: 'pill-assembled', failed: 'pill-failed', cancelled: 'pill-ghost'
};
const SHORT_MODE_NAMES = { news: 'Tin nóng', decode: 'Giải mã tiền bạc', daily_digest: 'Daily World Brief', trailer: 'Trailer video dài', cinematic: 'Kể chuyện điện ảnh' };

// Ngân sách từ do backend trả (`word_budget`, đúng theo mode + lang). Trước đây UI
// cắt chuỗi hint nên short tiếng Anh vẫn hiện ngân sách tiếng Việt.
function shortBudgetHint(short) {
  if (!short) return '';
  if (short.script && short.script.owner_script) return ' · kịch bản tự soạn, không áp ngân sách';
  const b = short.word_budget;
  if (!b || b.max == null) return '';
  return ` (ngân sách ${b.min}–${b.max} từ)`;
}

function goShortStudioPage() {
  setActiveNav('shorts');
  if ((window.location.hash || '') !== '#/shorts') {
    suppressHashSync = true;
    window.location.hash = '#/shorts';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  openShortStudioPage();
}

function openShortStudioPage() {
  hideTodoDetailDom();
  hideShortsPage();
  hideYoutubePage();
  hidePublishPage();
  hideChatPage();
  const page = document.getElementById('pageShortStudio');
  page.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'auto' });
  renderShortStudioShell();
  loadShorts({ force: true });
  loadNewsInbox();
  mpLoadInbox();
}

function hideTodoDetailDom() {
  const detailEl = document.getElementById('pageTodoDetail');
  if (detailEl) {
    cleanupVideosInContainer(detailEl);
    detailEl.classList.add('hidden');
    detailEl.innerHTML = '';
  }
  const listEl = document.getElementById('pageTodoList');
  if (listEl) listEl.classList.add('hidden');
}

async function loadShorts(opts = {}) {
  if (shortsLoaded && !opts.force) return;
  try {
    const r = await apiFetch('/api/v1/shorts', { timeoutMs: 20000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể tải danh sách Short'));
    const d = await r.json();
    allShorts = d.shorts || [];
    shortsLoaded = true;
    allShorts.forEach(s => { shortDetailsCache[s.id] = s; });
    updateOverviewStats();
    if (!document.getElementById('pageTodoList')?.classList.contains('hidden')) {
      renderFilteredTodos();
    }
  } catch (err) {
    console.error('[loadShorts]', err);
    showToast(err.message || 'Không thể tải danh sách Short', 'error');
  }
  if (!document.getElementById('pageShortStudio').classList.contains('hidden')) {
    // Giữ nội dung đang gõ khi danh sách tải xong re-render shell
    const saved = {
      src: document.getElementById('shortSourceText')?.value || '',
      note: document.getElementById('shortSourceNote')?.value || '',
      link: document.getElementById('shortSourceLink')?.value || '',
      target: document.getElementById('shortTargetSec')?.value || '',
      mode: shortMode,
      lang: shortLang
    };
    renderShortStudioShell();
    const ta = document.getElementById('shortSourceText');
    const note = document.getElementById('shortSourceNote');
    const link = document.getElementById('shortSourceLink');
    const target = document.getElementById('shortTargetSec');
    if (saved.src && ta) ta.value = saved.src;
    if (saved.note && note) note.value = saved.note;
    if (saved.link && link) link.value = saved.link;
    if (saved.target && target) target.value = saved.target;
    switchShortMode(saved.mode);
    switchShortLang(saved.lang);
    mpPaint();
  }
}

async function loadNewsInbox() {
  try {
    const langQs = newsLangFilter ? `&lang=${newsLangFilter}` : '';
    const r = await apiFetch(`/api/v1/news?status=new&limit=30${langQs}`, { timeoutMs: 15000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể tải hộp tin nóng'));
    const d = await r.json();
    shortNewsList = d.news || [];
  } catch (err) {
    console.warn('[loadNewsInbox]', err);
    shortNewsList = [];
  }
  const wrap = document.getElementById('shortNewsInbox');
  if (wrap) wrap.outerHTML = renderNewsInboxHtml();
}

function renderNewsInboxHtml() {
  const options = shortNewsList.map((n, i) => {
    const src = n.source ? ` · ${n.source}` : '';
    const tag = n.lang === 'en' ? ' [EN]' : '';
    return `<option value="${esc(n.id)}">#${String(i + 1).padStart(2, '0')} ${esc(n.title.slice(0, 90))}${src}${tag}</option>`;
  }).join('');
  const langBtn = (val, label) => `
    <button type="button" class="btn-xs ${newsLangFilter === val ? 'btn-primary' : ''}"
      onclick="filterNewsLang('${val}')">${label}</button>`;
  const first = shortNewsList[0];
  return `
  <div class="news-inbox" id="shortNewsInbox">
    <div class="news-inbox-head">
      <span>Hộp tin nóng vừa về (${shortNewsList.length} tin mới)</span>
      <span style="display:flex;gap:4px;align-items:center">
        ${langBtn('', 'Tất cả')}${langBtn('vi', 'VI')}${langBtn('en', 'EN')}
        <button type="button" class="btn-xs" onclick="syncNewsNow(this)">Sync tin ngay</button>
      </span>
    </div>
    ${shortNewsList.length ? `
      <div class="news-inbox-controls">
        <select id="newsPickSelect" aria-label="Chọn tin từ hộp tin nóng"
          onchange="updateNewsSourceLink()">${options}</select>
        <a id="newsSourceLink" class="btn-xs" href="${esc(first?.link || '#')}"
          target="_blank" rel="noopener" title="Mở bài gốc ở tab mới">Xem nguồn ↗</a>
        <button type="button" class="btn-xs btn-primary" onclick="useNewsItem()">Dùng tin này</button>
        <button type="button" class="btn-xs" onclick="pushNewsItem(this)" title="Gửi đúng tin đã chọn qua Telegram">Gửi Telegram</button>
        <button type="button" class="btn-xs" onclick="dismissNewsItem()" title="Ẩn tin này khỏi hộp tin">Gỡ</button>
      </div>
    ` : `
      <div style="font-size:12px;color:var(--fg-muted)">
        Chưa có tin nào trong hàng đợi. Bấm "Sync tin ngay" để lấy tin nóng mới nhất
        (Google News RSS VI + EN quốc tế: BBC, CNBC, Al Jazeera, Guardian),
        hoặc dán tin vắn trực tiếp vào ô bên dưới.
        Tin chỉ được làm Short khi bạn chọn "Dùng tin này".
      </div>
    `}
  </div>`;
}

function updateNewsSourceLink() {
  const sel = document.getElementById('newsPickSelect');
  const item = shortNewsList.find(n => n.id === sel?.value);
  const a = document.getElementById('newsSourceLink');
  if (a && item?.link) a.href = item.link;
}

function filterNewsLang(lang) {
  newsLangFilter = lang;
  loadNewsInbox();
}

async function syncNewsNow(btnEl) {
  const prev = btnEl ? btnEl.textContent : '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang sync...'; }
  try {
    const r = await apiFetch('/api/v1/news/sync', { method: 'POST', timeoutMs: 120000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể sync tin nóng'));
    const d = await r.json();
    showToast(`Sync xong: ${d.new} tin mới, ${d.pushed} tin đẩy Telegram` +
      (d.failed_queries.length ? ` (${d.failed_queries.length} nguồn lỗi)` : ''), 'ok', 5000);
    await loadNewsInbox();
  } catch (err) {
    showToast(err.message || 'Lỗi sync tin nóng', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Sync tin ngay'; }
  }
}

async function runDailyDigestNow(btnEl) {
  const prev = btnEl ? btnEl.textContent : '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '⏳ Đang gom 12–15 tin & render...'; }
  try {
    const r = await apiFetch('/api/v1/shorts/daily-digest/run', { method: 'POST', timeoutMs: 600000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể tạo Daily Digest'));
    const d = await r.json();
    showToast(`Xong Daily Brief (${d.stories_count || 12} tin · ${Number(d.duration_s || 0).toFixed(1)}s) — đã gửi Telegram!`, 'ok', 6000);
    await loadShorts({ force: true });
    if (d.short_id) openShortDetailPage(d.short_id);
  } catch (err) {
    showToast(err.message || 'Lỗi tạo Daily Digest', 'error', 5000);
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || '🌍 Tạo Daily Digest EN ngay'; }
  }
}

function useNewsItem() {
  const sel = document.getElementById('newsPickSelect');
  const item = shortNewsList.find(n => n.id === sel?.value);
  if (!item) return;
  const body = [item.title, item.summary].filter(Boolean).join('\n');
  const ta = document.getElementById('shortSourceText');
  const note = document.getElementById('shortSourceNote');
  const link = document.getElementById('shortSourceLink');
  if (ta) ta.value = body;
  if (note && item.source && !note.value) {
    note.value = item.lang === 'en' ? `Source: ${item.source}` : `Nguồn: ${item.source}`;
  }
  if (link && item.link) link.value = item.link;
  switchShortLang(item.lang === 'en' ? 'en' : 'vi');  // tin EN → tự chuyển mảng quốc tế
  if (ta) { ta.focus(); ta.scrollIntoView({ behavior: 'auto', block: 'center' }); }
  showToast('Đã điền tin vào ô nhập kịch bản (kèm link lấy ảnh gốc)', 'ok', 2600);
}

function switchShortLang(lang) {
  shortLang = lang === 'en' ? 'en' : 'vi';
  document.querySelectorAll('.short-lang-tab').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lang') === shortLang);
  });
  const hint = document.getElementById('shortLangHint');
  if (hint) {
    hint.textContent = shortLang === 'en'
      ? 'Mảng quốc tế: voiceover tiếng Anh (Andrew), prompt EN, visual Pexels'
      : 'Tiếng Việt: voiceover HoaiMy, prompt VI';
  }
}

async function pushNewsItem(btnEl) {
  const sel = document.getElementById('newsPickSelect');
  if (!sel?.value) return;
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang gửi...'; }
  try {
    const r = await apiFetch(`/api/v1/news/${sel.value}/push`, { method: 'POST' });
    if (!r.ok) throw new Error(await extractApiError(r, 'Gửi Telegram thất bại'));
    showToast('Đã gửi tin đã chọn lên Telegram', 'ok', 2600);
    await loadNewsInbox();
  } catch (err) {
    showToast(err.message || 'Gửi Telegram thất bại', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Gửi Telegram'; }
  }
}

async function dismissNewsItem() {
  const sel = document.getElementById('newsPickSelect');
  if (!sel?.value) return;
  try {
    const r = await apiFetch(`/api/v1/news/${sel.value}/dismiss`, { method: 'POST' });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể gỡ tin'));
    await loadNewsInbox();
    showToast('Đã gỡ tin khỏi hộp tin nóng', 'ok', 2200);
  } catch (err) {
    showToast(err.message || 'Lỗi gỡ tin', 'error');
  }
}

function switchShortMode(mode) {
  shortMode = mode;
  document.querySelectorAll('.short-mode-tab').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-mode') === mode);
  });
  const target = document.getElementById('shortTargetSec');
  if (target) target.value = String(SHORT_MODE_META[mode].target);
  // Trailer chỉ hỗ trợ EN (video dài là EN explainer) — tự chuyển mảng quốc tế
  if (mode === 'trailer' && shortLang !== 'en') switchShortLang('en');
}

async function createShortForm(ev) {
  ev.preventDefault();
  const ta = document.getElementById('shortSourceText');
  const source = (ta?.value || '').trim();
  if (!source) {
    showToast('Chưa có nội dung: dán tin vắn/chủ đề hoặc chọn tin từ hộp tin nóng', 'warn');
    return;
  }
  const btn = document.getElementById('shortCreateBtn');
  const prev = btn?.textContent || '';
  if (btn) { btn.disabled = true; btn.textContent = 'Đang viết kịch bản...'; }
  try {
    const r = await apiFetch('/api/v1/shorts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: shortMode,
        lang: shortLang,
        source_text: source,
        source_note: document.getElementById('shortSourceNote')?.value.trim() || '',
        source_link: document.getElementById('shortSourceLink')?.value.trim() || '',
        target_seconds: Number(document.getElementById('shortTargetSec')?.value || 0) || null
      }),
      timeoutMs: 180000
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Tạo kịch bản thất bại'));
    const short = await r.json();
    shortDetailsCache[short.id] = short;
    shortsLoaded = false;
    showToast(`Đã tạo kịch bản (${short.word_count} từ) — duyệt từng câu rồi tổng hợp`, 'ok', 4200);
    openShortDetailPage(short.id);
  } catch (err) {
    showToast(err.message || 'Tạo kịch bản thất bại', 'error');
  } finally {
    if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = prev || 'Tạo kịch bản'; }
  }
}

function renderShortStudioShell() {
  const page = document.getElementById('pageShortStudio');
  const readyCount = allShorts.filter(s => s.status === 'ready').length;
  const producingCount = allShorts.filter(s => s.status === 'producing').length;
  const failedCount = allShorts.filter(s => s.status === 'failed').length;
  page.innerHTML = `
  <section class="overview-bar">
    <div class="stat-card">
      <div class="stat-label">Tổng Short</div>
      <div class="stat-value">${allShorts.length}</div>
      <div class="stat-sub">Tin nóng &amp; Giải mã tiền bạc 9:16</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Đã có video</div>
      <div class="stat-value" style="color:var(--ok)">${readyCount}</div>
      <div class="stat-sub">Sẵn sàng tải lên nền tảng</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Đang tổng hợp</div>
      <div class="stat-value" style="color:var(--run)">${producingCount}</div>
      <div class="stat-sub">Voice → ảnh → ghép MP4</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Hỏng</div>
      <div class="stat-value" style="color:var(--held)">${failedCount}</div>
      <div class="stat-sub">Cần xử lý — có lý do chi tiết</div>
    </div>
  </section>

  <section class="panel" id="mpInboxPanel" style="margin-bottom:12px">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">
          🛡 Inbox đề xuất Short (muse)
          <span id="mpBadgeAwaiting" class="hidden" style="background:var(--accent);color:var(--accent-ink);border-radius:9px;font-size:10px;padding:1px 6px"></span>
          <span id="mpBadgeReviewing" class="hidden" style="background:var(--fg-muted);color:var(--bg);border-radius:9px;font-size:10px;padding:1px 6px;margin-left:4px"></span>
        </h3>
        <div class="panel-subtitle">Muse agent tự đề xuất ý tưởng → 3 agent bất kỳ chấm chéo (muse tự lên lịch vào web mỗi giờ). Đề xuất đạt ≥2/3 chấm ≥9.5 → chờ bạn duyệt → needs_script vào hàng chờ ưu tiên gen kịch bản.</div>
      </div>
      <div class="rb-actions">
        <button type="button" class="btn-primary btn-sm" onclick="mpTakeReviewJob(this)">🧪 Chấm đề xuất tiếp theo</button>
        <button type="button" class="btn-xs" onclick="mpLoadInbox()">🔄 Làm mới</button>
      </div>
    </div>
    <div id="mpInboxList"><div class="cmt-meta">Đang tải inbox đề xuất...</div></div>
    <div id="mpWorkArea"></div>
  </section>

  <div class="shorts-layout">
    <div>
      <section class="panel">
        <div class="panel-header">
          <div>
            <h3 class="panel-title">Short Studio</h3>
            <div class="panel-subtitle">Tạo kịch bản AI, upload MP4 9:16, hoặc chạy Daily Digest EN (@StickSto · tự động 06:30 sáng giờ Mỹ → gửi Telegram duyệt).</div>
          </div>
          <button type="button" class="btn-sm btn-primary" onclick="runDailyDigestNow(this)" title="Gom 12–15 tin quốc tế EN mới nhất, render MP4 <180s và gửi Telegram">🌍 Tạo Daily Digest EN ngay</button>
        </div>
        <div class="short-studio-switcher" role="tablist" aria-label="Phương thức tạo Short">
          <button type="button" class="short-studio-tab ${shortCreateTab === 'ai' ? 'active' : ''}" onclick="switchShortCreateTab('ai')">✨ Tạo từ kịch bản AI</button>
          <button type="button" class="short-studio-tab ${shortCreateTab === 'upload' ? 'active' : ''}" onclick="switchShortCreateTab('upload')">📤 Upload sản phẩm (.MP4)</button>
          <button type="button" class="short-studio-tab ${shortCreateTab === 'audio' ? 'active' : ''}" onclick="switchShortCreateTab('audio')">🎙 Nạp audio + shot</button>
        </div>

        ${shortCreateTab === 'audio' ? `
          <div class="short-form-grid">
            <div class="short-form-row">
              <label for="shortAudioFile">File audio (bắt buộc):</label>
              <input type="file" id="shortAudioFile" accept="audio/*" onchange="onShortAudioFilePicked(this)"/>
              <span id="shortAudioFileName" class="mono" style="font-size:11.5px;color:var(--fg-muted)">
                ${shortAudioFileObj ? `📎 ${esc(shortAudioFileObj.name)} (${(shortAudioFileObj.size / 1048576).toFixed(2)} MB)` : 'Chưa chọn file'}
              </span>
            </div>
            <div class="short-form-row">
              <label for="shortAudioTitle">Tiêu đề (tùy chọn):</label>
              <input type="text" id="shortAudioTitle" placeholder="VD: Sáu giờ sáng, thành phố còn ngủ" maxlength="200"
                     style="flex:1;min-width:220px" value="${esc(shortAudioTitleText)}"
                     oninput="shortAudioTitleText=this.value"/>
            </div>
            <div class="short-form-row">
              <label for="shortAudioMode">Mode:</label>
              <select id="shortAudioMode" onchange="shortAudioMode=this.value">
                ${Object.entries(SHORT_MODE_META).map(([k, m]) => `
                  <option value="${k}" ${shortAudioMode === k ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}
              </select>
              <label for="shortAudioLang">Ngôn ngữ:</label>
              <select id="shortAudioLang" onchange="shortAudioLang=this.value">
                <option value="vi" ${shortAudioLang === 'vi' ? 'selected' : ''}>Tiếng Việt</option>
                <option value="en" ${shortAudioLang === 'en' ? 'selected' : ''}>English</option>
              </select>
            </div>
            <div class="short-form-row" style="align-items:flex-start">
              <label for="shortAudioShots">Danh sách shot:</label>
              <textarea id="shortAudioShots" rows="6" placeholder="0-4.2 | Sáu giờ sáng, thành phố còn ngủ&#10;4.2-8 | Xe buýt lăn bánh qua quảng trường"
                        style="flex:1;min-width:240px" oninput="shortAudioShotsText=this.value">${esc(shortAudioShotsText)}</textarea>
            </div>
            <div id="shortAudioErr" class="hidden" style="color:var(--held);font-size:12px;font-weight:600"></div>
            <div class="short-form-row">
              <button type="button" id="shortAudioSubmitBtn" class="btn-primary" onclick="submitShortAudioForm(this)">🎙 Nạp audio &amp; tạo job</button>
              <span style="font-size:11.5px;color:var(--fg-muted)">Audio do bạn thu. Timing shot phải khớp audio (giây). Hệ thống không tự nhận diện giọng.</span>
            </div>
            ${shortAudioCreatedId ? `
              <div class="short-form-row">
                <span class="pill pill-ok mono">${esc(shortAudioCreatedId)}</span>
                <span style="font-size:11.5px;color:var(--fg-muted)">Short đã tạo từ audio owner.</span>
                <button type="button" class="btn-sm btn-primary" onclick="publishShortJob('${esc(shortAudioCreatedId)}', this)">Phát hành job ngay</button>
                <button type="button" class="btn-sm" onclick="openShortDetailPage('${esc(shortAudioCreatedId)}')">Mở chi tiết</button>
              </div>` : ''}
          </div>
        ` : ''}

        ${shortCreateTab === 'upload' ? `
          <div class="short-form-grid">
            <input type="file" id="shortUploadFile" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" class="hidden" onchange="onShortUploadFilePicked(this.files[0])"/>
            <div class="short-upload-dropzone" id="shortUploadDropzone"
                 onclick="document.getElementById('shortUploadFile').click()"
                 ondragover="event.preventDefault();this.classList.add('is-dragover')"
                 ondragleave="this.classList.remove('is-dragover')"
                 ondrop="handleShortUploadDrop(event)">
              <div class="upload-icon">🎬</div>
              <div class="upload-title">${shortUploadFileObj ? 'Đã chọn file — bấm hoặc kéo thả để đổi file khác' : 'Kéo thả file MP4 9:16 vào đây hoặc bấm để chọn'}</div>
              <div class="upload-sub">Hỗ trợ MP4, MOV, WEBM · Tự động nhận diện thời lượng &amp; xem trước ngay bên phải</div>
              ${shortUploadFileObj ? `<span class="short-upload-filechip">📎 ${esc(shortUploadFileObj.name)} (${(shortUploadFileObj.size / 1048576).toFixed(2)} MB)</span>` : ''}
            </div>
            <div class="short-form-row">
              <label for="shortUploadTitle">Tiêu đề Short:</label>
              <input type="text" id="shortUploadTitle" placeholder="VD: SpaceX Catches Giant Rocket Mid-Air" maxlength="200" style="flex:1;min-width:220px" value="${esc(shortUploadFileObj ? shortUploadFileObj.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ') : '')}"/>
            </div>
            <div class="short-mode-tabs" role="group" aria-label="Chọn chế độ Short">
              ${Object.entries(SHORT_MODE_META).map(([k, m]) => `
                <button type="button" class="btn short-mode-tab ${shortMode === k ? 'active' : ''}" data-mode="${k}" onclick="switchShortMode('${k}')">
                  <span><b>${esc(m.label)}</b><small>${esc(m.hint)}</small></span>
                </button>`).join('')}
            </div>
            <div class="short-mode-tabs" role="group" aria-label="Chọn ngôn ngữ Short">
              <button type="button" class="btn short-lang-tab ${shortLang === 'vi' ? 'active' : ''}" data-lang="vi" onclick="switchShortLang('vi')">
                <span><b>Tiếng Việt</b><small>Nhãn VI</small></span>
              </button>
              <button type="button" class="btn short-lang-tab ${shortLang === 'en' ? 'active' : ''}" data-lang="en" onclick="switchShortLang('en')">
                <span><b>English (Quốc tế)</b><small>Nhãn EN</small></span>
              </button>
            </div>
            <div class="short-form-row">
              <label for="shortUploadNote">Nguồn tin:</label>
              <input type="text" id="shortUploadNote" placeholder="VD: SpaceX / Wikimedia Commons" maxlength="300" style="flex:1;min-width:160px"/>
            </div>
            <div class="short-form-row">
              <label for="shortUploadDesc">Mô tả đăng bài (tùy chọn):</label>
              <input type="text" id="shortUploadDesc" placeholder="Caption / hashtag khi đăng TikTok, Shorts..." maxlength="1000" style="flex:1;min-width:220px"/>
            </div>
            <div class="short-form-row">
              <button type="button" id="shortUploadSubmitBtn" class="btn-primary" onclick="submitShortUpload(this)">📤 Upload &amp; Xuất bản Short</button>
              <span style="font-size:11.5px;color:var(--fg-muted)">Video sẽ ở trạng thái Sẵn sàng ngay sau khi tải lên.</span>
            </div>
          </div>
        ` : shortCreateTab === 'ai' ? `
          <div class="short-mode-tabs" role="group" aria-label="Chọn chế độ Short">
            ${Object.entries(SHORT_MODE_META).map(([k, m]) => `
              <button type="button" class="btn short-mode-tab ${shortMode === k ? 'active' : ''}" data-mode="${k}" onclick="switchShortMode('${k}')">
                <span><b>${esc(m.label)}</b><small>${esc(m.hint)}</small></span>
              </button>`).join('')}
          </div>
          <div class="short-mode-tabs" role="group" aria-label="Chọn ngôn ngữ Short" style="margin-top:8px">
            <button type="button" class="btn short-lang-tab ${shortLang === 'vi' ? 'active' : ''}" data-lang="vi" onclick="switchShortLang('vi')">
              <span><b>Tiếng Việt</b><small>voiceover vi-VN-HoaiMy</small></span>
            </button>
            <button type="button" class="btn short-lang-tab ${shortLang === 'en' ? 'active' : ''}" data-lang="en" onclick="switchShortLang('en')">
              <span><b>English (Quốc tế)</b><small>voiceover en-US-Andrew · Pexels b-roll</small></span>
            </button>
          </div>
          <div id="shortLangHint" style="font-size:11.5px;color:var(--fg-muted);margin-top:4px">
            ${shortLang === 'en' ? 'Mảng quốc tế: voiceover tiếng Anh (Andrew), prompt EN, visual Pexels' : 'Tiếng Việt: voiceover HoaiMy, prompt VI'}
          </div>
          ${renderNewsInboxHtml()}
          <div class="short-form-grid">
            <textarea id="shortSourceText" rows="5" placeholder="Dán nội dung bài báo/tin vắn (mode Tin nóng) hoặc chủ đề giải mã (vd: 'Càng tiết kiệm càng nghèo')..." maxlength="14000"></textarea>
            <div class="short-form-row">
              <label for="shortSourceLink">Link bài báo / video dài (tùy chọn — ảnh gốc minh họa; trailer: URL video dài trên YouTube):</label>
              <input type="text" id="shortSourceLink" placeholder="https://... (trailer: link video dài — dùng cho CTA/description)" maxlength="1000" style="flex:1;min-width:220px"/>
            </div>
            <div class="short-form-row">
              <label for="shortSourceNote">Nguồn tin (ghi trên video):</label>
              <input type="text" id="shortSourceNote" placeholder="Nguồn: VnExpress..." maxlength="300" style="flex:1;min-width:160px"/>
              <label for="shortTargetSec">Giây mục tiêu:</label>
              <input type="number" id="shortTargetSec" value="${SHORT_MODE_META[shortMode].target}" min="10" max="180" style="width:74px"/>
            </div>
            <div class="short-form-row">
              <button type="button" id="shortCreateBtn" class="btn-primary" onclick="createShortForm(event)">Tạo kịch bản</button>
              <span style="font-size:11.5px;color:var(--fg-muted)">Kịch bản sync (10–30s) — chưa duyệt sẽ không render.</span>
            </div>
          </div>
        ` : ''}
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h3 class="panel-title">Danh sách Short (${allShorts.length})</h3>
            <div class="panel-subtitle">Bấm vào một Short để duyệt kịch bản, nghe voice, xem khung 9:16 và tải video + gói đăng bài.</div>
          </div>
          <button type="button" class="btn-xs" onclick="loadShorts({force:true})">Làm mới</button>
        </div>
        ${allShorts.length ? `
          <div class="shorts-grid">
            ${allShorts.map(s => `
              <article class="short-card ${s.status === 'ready' ? 'is-ready' : (s.status === 'failed' ? 'is-failed' : (s.status === 'producing' ? 'is-producing' : ''))}"
                role="button" tabindex="0" data-kb-click="1" aria-label="Mở Short: ${esc(s.title)}"
                onclick="openShortDetailPage('${esc(s.id)}')">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap">
                  <span class="pill pill-ghost mono">${esc(s.id)}</span>
                  <span class="pill ${SHORT_STATUS_PILL[s.status] || 'pill-ghost'}">${esc(s.status_label)}</span>
                </div>
                <h4 class="short-card-title" title="${esc(s.title)}">${esc(s.title)}</h4>
                <div class="short-card-meta">
                  <span class="pill pill-accent">${esc(SHORT_MODE_NAMES[s.mode] || s.mode || 'Upload MP4')}</span>
                  ${s.lang === 'en' ? '<span class="pill pill-ghost mono">EN</span>' : ''}
                  ${s.duration_s > 0 ? `<span class="mono">${Number(s.duration_s).toFixed(1)}s</span>` : ''}
                  <span class="mono">${Number(s.word_count || 0)} từ</span>
                  ${s.video_asset_id ? `<span class="pill pill-ok">MP4 9:16</span>` : ''}
                </div>
                ${s.youtube_upload ? `<div style="margin-top:6px">${renderTodoYoutubeStatusBanner(s, 'short')}</div>` : ''}
                <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px">
                  ${s.video_asset_id ? `<button type="button" class="btn-xs" onclick="previewShortOnPhone('${esc(s.id)}', event)">▶ Xem</button>` : ''}
                  ${renderYoutubeActionButton(s, 'short', 'btn-primary btn-xs')}
                </div>
              </article>`).join('')}
          </div>
        ` : `
          <div style="text-align:center;padding:28px;color:var(--fg-muted);border:1px dashed var(--border-strong);border-radius:var(--r)">
            Chưa có Short nào. Tạo kịch bản AI hoặc Upload sản phẩm MP4 bằng bảng phía trên.
          </div>
        `}
      </section>
    </div>

    <div class="phone-side">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h3 class="panel-title">Khung xem trước 9:16</h3>
            <div class="panel-subtitle">${shortCreateTab === 'upload' && shortUploadPreviewUrl ? 'Đang xem trước file MP4 vừa chọn trên máy.' : (shortQuickPreviewId ? `Đang phát: ${esc(shortQuickPreviewId)}` : 'Bấm "▶ Xem" trên thẻ Short hoặc chọn file MP4 để xem trước.')}</div>
          </div>
          ${shortQuickPreviewId ? `<button type="button" class="btn-xs btn-primary" onclick="openShortDetailPage('${esc(shortQuickPreviewId)}')">Chi tiết</button>` : ''}
        </div>
        <div class="phone-frame" id="shortMainPhoneFrame">
          ${shortCreateTab === 'upload' && shortUploadPreviewUrl
            ? `<video controls autoplay muted loop playsinline src="${shortUploadPreviewUrl}"></video>`
            : (shortQuickPreviewId
                ? `<video controls autoplay playsinline src="/api/v1/shorts/${esc(shortQuickPreviewId)}/video"></video>`
                : `<div class="phone-empty">
                     <span class="empty-slot-badge">CHƯA CHỌN SHORT</span>
                     <div>Bấm vào một Short trong danh sách hoặc chọn file MP4 để xem trước tại đây.</div>
                   </div>`)}
        </div>
      </section>
    </div>
  </div>`;
}

function switchShortCreateTab(tab) {
  shortCreateTab = (tab === 'upload' || tab === 'audio') ? tab : 'ai';
  renderShortStudioShell();
}

function onShortUploadFilePicked(file) {
  if (!file) return;
  if (shortUploadPreviewUrl) {
    try { URL.revokeObjectURL(shortUploadPreviewUrl); } catch (_) {}
  }
  shortUploadFileObj = file;
  shortUploadPreviewUrl = URL.createObjectURL(file);
  renderShortStudioShell();
}

function handleShortUploadDrop(ev) {
  ev.preventDefault();
  ev.currentTarget?.classList.remove('is-dragover');
  const file = ev.dataTransfer?.files?.[0];
  if (file) onShortUploadFilePicked(file);
}

function previewShortOnPhone(shortId, ev) {
  if (ev) ev.stopPropagation();
  shortQuickPreviewId = shortId;
  renderShortStudioShell();
}

// --- Audio owner: nạp audio + shot list (không TTS) ---
function onShortAudioFilePicked(inputEl) {
  const file = inputEl?.files?.[0] || null;
  shortAudioFileObj = file;
  const label = document.getElementById('shortAudioFileName');
  if (label) {
    label.textContent = file
      ? `📎 ${file.name} (${(file.size / 1048576).toFixed(2)} MB)`
      : 'Chưa chọn file';
  }
}

function shortAudioShowError(msg) {
  const errEl = document.getElementById('shortAudioErr');
  if (errEl) {
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
  showToast(msg, 'error', 5200);
}

// Nhận cả JSON list ([{text,start_sec,end_sec}]) lẫn text mỗi dòng "start-end | nội dung"
function parseShortAudioShots(raw) {
  const text = String(raw || '').trim();
  if (!text) return { shots: [], error: 'Danh sách shot đang trống — cần ít nhất 1 shot.' };
  let items = null;
  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text);
      const list = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.shots) ? parsed.shots : null);
      if (list) {
        items = list.map(s => ({
          start_sec: Number(s?.start_sec),
          end_sec: Number(s?.end_sec),
          text: String(s?.text ?? '').trim()
        }));
      }
    } catch (_) { items = null; }
  }
  if (!items) {
    items = [];
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    for (const line of lines) {
      const m = line.match(/^([\d]+(?:[.,]\d+)?)\s*[-–—]\s*([\d]+(?:[.,]\d+)?)\s*[|｜]\s*(.+)$/);
      if (!m) {
        return { shots: [], error: `Dòng không đúng định dạng "start-end | nội dung": ${line.slice(0, 60)}` };
      }
      items.push({
        start_sec: Number(m[1].replace(',', '.')),
        end_sec: Number(m[2].replace(',', '.')),
        text: m[3].trim()
      });
    }
  }
  if (!items.length) return { shots: [], error: 'Cần ít nhất 1 shot có nội dung.' };
  for (let i = 0; i < items.length; i++) {
    const cur = items[i];
    if (!Number.isFinite(cur.start_sec) || !Number.isFinite(cur.end_sec)) {
      return { shots: [], error: `Shot #${i + 1}: mốc thời gian không hợp lệ.` };
    }
    if (!cur.text) return { shots: [], error: `Shot #${i + 1}: nội dung rỗng.` };
    if (cur.end_sec <= cur.start_sec) {
      return { shots: [], error: `Shot #${i + 1}: end phải lớn hơn start.` };
    }
    if (i > 0) {
      const prev = items[i - 1];
      if (cur.start_sec < prev.start_sec) {
        return { shots: [], error: `Shot #${i + 1}: start phải tăng dần.` };
      }
      if (cur.start_sec < prev.end_sec - 0.05) {
        return { shots: [], error: `Shot #${i + 1} chồng lấn shot #${i} (start ${cur.start_sec}s < end ${prev.end_sec}s).` };
      }
    }
  }
  return { shots: items, error: '' };
}

async function submitShortAudioForm(btnEl) {
  const file = shortAudioFileObj || document.getElementById('shortAudioFile')?.files?.[0] || null;
  if (!file) { shortAudioShowError('Vui lòng chọn file audio trước khi nạp.'); return; }
  const parsed = parseShortAudioShots(document.getElementById('shortAudioShots')?.value || '');
  if (parsed.error) { shortAudioShowError(parsed.error); return; }
  const errEl = document.getElementById('shortAudioErr');
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }

  const title = (document.getElementById('shortAudioTitle')?.value || '').trim();
  const mode = shortAudioMode || 'cinematic';
  const lang = shortAudioLang || 'vi';
  const prev = btnEl?.textContent || '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner-ring" style="width:12px;height:12px;border-width:2px;border-top-color:var(--accent-ink)"></span> Đang nạp...';
  }
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('shots', JSON.stringify(parsed.shots));
    if (title) fd.append('title', title);
    fd.append('mode', mode);
    fd.append('lang', lang);
    const r = await apiFetch('/api/v1/shorts/audio', { method: 'POST', body: fd, timeoutMs: 180000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi nạp audio + shot'));
    const created = await r.json();
    shortDetailsCache[created.id] = created;
    shortsLoaded = false;
    shortAudioCreatedId = created.id;
    shortAudioFileObj = null;
    shortAudioShotsText = '';
    shortAudioTitleText = '';
    const nShots = (created.scenes || parsed.shots).length;
    showToast(`Đã nạp audio — ${nShots} shot`, 'ok', 4000);
    openShortDetailPage(created.id);
  } catch (err) {
    shortAudioShowError(err.message || 'Lỗi nạp audio + shot');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || '🎙 Nạp audio & tạo job'; }
  }
}

async function clearShortAudioOwner(shortId, btnEl) {
  if (!window.confirm('Bỏ audio owner và quay lại luồng TTS? Timing shot hiện tại sẽ bị xoá.')) return;
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang bỏ audio...'; }
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/audio`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi bỏ audio owner'));
    const updated = await r.json();
    shortDetailsCache[shortId] = updated;
    shortsLoaded = false;
    if (shortAudioCreatedId === shortId) shortAudioCreatedId = '';
    renderShortDetailPage(updated);
    showToast('Đã bỏ audio owner — short quay lại luồng TTS', 'ok', 3200);
  } catch (err) {
    showToast(err.message || 'Lỗi bỏ audio owner', 'error', 5200);
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Bỏ audio owner'; }
  }
}

async function submitShortUpload(btnEl) {
  if (!shortUploadFileObj) {
    showToast('Vui lòng chọn file MP4 9:16 trước khi upload', 'error');
    return;
  }
  const title = (document.getElementById('shortUploadTitle')?.value || '').trim();
  const sourceNote = (document.getElementById('shortUploadNote')?.value || '').trim();
  const description = (document.getElementById('shortUploadDesc')?.value || '').trim();
  const prev = btnEl?.textContent || '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = '<span class="spinner-ring" style="width:12px;height:12px;border-width:2px;border-top-color:var(--accent-ink)"></span> Đang tải lên...';
  }
  try {
    const fd = new FormData();
    fd.append('file', shortUploadFileObj, shortUploadFileObj.name);
    if (title) fd.append('title', title);
    fd.append('mode', shortMode);
    fd.append('lang', shortLang);
    if (sourceNote) fd.append('source_note', sourceNote);
    if (description) fd.append('description', description);
    const r = await apiFetch('/api/v1/shorts/upload', { method: 'POST', body: fd, timeoutMs: 180000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi upload Short MP4'));
    const created = await r.json();
    if (shortUploadPreviewUrl) {
      try { URL.revokeObjectURL(shortUploadPreviewUrl); } catch (_) {}
    }
    shortUploadFileObj = null;
    shortUploadPreviewUrl = '';
    shortQuickPreviewId = created.id;
    shortsLoaded = false;
    shortDetailsCache[created.id] = created;
    showToast(`Đã upload Short ${created.id} (${Number(created.duration_s || 0).toFixed(1)}s) — Sẵn sàng!`, 'ok', 3500);
    openShortDetailPage(created.id);
  } catch (err) {
    showToast(err.message || 'Lỗi upload Short MP4', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || '📤 Upload & Xuất bản Short'; }
  }
}

async function uploadExistingShortVideo(shortId, inputEl) {
  const file = inputEl?.files?.[0];
  if (!file) return;
  showToast(`Đang upload ${file.name}...`, 'info', 4000);
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/video`, {
      method: 'POST', body: fd, timeoutMs: 180000
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi upload video MP4'));
    const updated = await r.json();
    shortDetailsCache[shortId] = updated;
    shortsLoaded = false;
    renderShortDetailPage(updated);
    showToast(`Đã cập nhật MP4 thành phẩm (${Number(updated.duration_s || 0).toFixed(1)}s)`, 'ok', 3000);
  } catch (err) {
    showToast(err.message || 'Lỗi upload video MP4', 'error');
  } finally {
    if (inputEl) inputEl.value = '';
  }
}

async function openShortDetailPage(shortId, initialTab = '') {
  setActiveNav('shorts');
  activeDetailShortId = shortId;
  if ((window.location.hash || '') !== `#/shorts/${encodeURIComponent(shortId)}`) {
    suppressHashSync = true;
    window.location.hash = `#/shorts/${encodeURIComponent(shortId)}`;
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  hideTodoDetailDom();
  const page = document.getElementById('pageShortStudio');
  page.classList.remove('hidden');
  page.innerHTML = `
    <div class="loading-panel">
      <div style="display:inline-flex;align-items:center;gap:10px;font-weight:600">
        <span class="spinner-ring"></span> Đang tải Short ${esc(shortId)}...
      </div>
    </div>`;
  window.scrollTo({ top: 0, behavior: 'auto' });
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}`, { timeoutMs: 20000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể tải Short'));
    const short = await r.json();
    shortDetailsCache[shortId] = short;
    renderShortDetailPage(short);
  } catch (err) {
    page.innerHTML = `
      <div class="error-state-panel">
        <div style="font-weight:700;margin-bottom:6px">Không thể tải Short</div>
        <div style="font-size:12px;margin-bottom:12px">${esc(err.message || 'Lỗi không xác định')}</div>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <button type="button" class="btn-primary" onclick="openShortDetailPage('${esc(shortId)}')">Thử tải lại</button>
          <button type="button" onclick="goShortStudioPage()">Về danh sách Short</button>
        </div>
      </div>`;
  }
}

function renderShortDetailPage(short) {
  const page = document.getElementById('pageShortStudio');
  const id = short.id;
  const editable = ['scripted', 'failed'].includes(short.status);
  const scenes = short.scenes || [];
  const hasTimeline = scenes.length > 0 && Number(scenes[0].end_sec) > 0;
  const modeMeta = SHORT_MODE_META[short.mode] || SHORT_MODE_META.news;
  const ownerAudio = short.audio_source === 'owner';

  const scriptEdHtml = `
    <section class="panel">
      <div class="panel-header">
        <div>
          <h3 class="panel-title">Kịch bản — ${esc(SHORT_MODE_NAMES[short.mode] || short.mode)}
            <span class="pill ${SHORT_STATUS_PILL[short.status] || 'pill-ghost'}">${esc(short.status_label)}</span>
            ${ownerAudio ? '<span class="pill pill-accent">Audio owner</span>' : ''}
          </h3>
          <div class="panel-subtitle">
            <span class="mono">${esc(id)}</span> ·
            <span class="mono">${Number(short.word_count || 0)} từ</span>${shortBudgetHint(short)}
            · duyệt từng câu trước khi tổng hợp
          </div>
        </div>
        <div class="todo-actions">
          <button type="button" class="btn-sm" onclick="goShortStudioPage()">← Danh sách</button>
        </div>
      </div>

      ${short.status === 'failed' && short.error_detail ? `
        <div class="status-banner is-error" role="alert">
          <div class="status-banner-main">
            <div style="margin-top:6px;display:flex"><span class="status-dot" style="background:var(--held)"></span></div>
            <div>
              <div class="status-banner-title">Tổng hợp thất bại — lý do chi tiết</div>
              <div class="status-banner-detail">${esc(short.error_detail)}</div>
            </div>
          </div>
          <div class="status-banner-actions">
            <button type="button" class="btn-primary btn-xs" onclick="produceShort('${esc(id)}', this)">Thử lại</button>
          </div>
        </div>
      ` : ''}

      <div class="short-form-grid">
        <div class="short-form-row">
          <label for="shortTitleEd">Tiêu đề:</label>
          <input type="text" id="shortTitleEd" value="${esc(short.title)}" maxlength="200" style="flex:1;min-width:200px" ${editable ? '' : 'disabled'}/>
        </div>
        <div class="beat-row">
          <div class="beat-row-head"><span>HOOK (0–3s) — mở giật gân</span><span class="mono">${countWords(short.script?.hook || '')} từ</span></div>
          <textarea id="shortHookEd" ${editable ? '' : 'disabled'}>${esc(short.script?.hook || '')}</textarea>
        </div>
        ${(short.script?.beats || []).map((b, i) => `
          <div class="beat-row">
            <div class="beat-row-head"><span>BEAT #${String(i + 1).padStart(2, '0')}</span><span class="mono">${countWords(b)} từ</span></div>
            <textarea class="short-beat-ed" data-beat="${i}" ${editable ? '' : 'disabled'}>${esc(b)}</textarea>
          </div>`).join('')}
        <div class="beat-row">
          <div class="beat-row-head"><span>CLOSING — câu hỏi mở / chốt loop</span><span class="mono">${countWords(short.script?.closing || '')} từ</span></div>
          <textarea id="shortClosingEd" ${editable ? '' : 'disabled'}>${esc(short.script?.closing || '')}</textarea>
        </div>
        <div class="short-form-row">
          <label for="shortDescEd">Mô tả đăng bài:</label>
          <input type="text" id="shortDescEd" value="${esc(short.script?.description || '')}" maxlength="1000" style="flex:1;min-width:200px" ${editable ? '' : 'disabled'}/>
        </div>
        <div class="short-form-row">
          <label for="shortNoteEd">Nguồn tin:</label>
          <input type="text" id="shortNoteEd" value="${esc(short.script?.source_note || '')}" maxlength="300" style="flex:1;min-width:200px" ${editable ? '' : 'disabled'}/>
        </div>
        <div class="short-form-row">
          ${editable ? '<button type="button" class="btn-sm" onclick="saveShortScript(\'' + esc(id) + '\', this)">Lưu chỉnh sửa</button>' : ''}
          <button type="button" id="shortProduceBtn" class="btn-primary btn-sm" onclick="produceShort('${esc(id)}', this)">
            ${short.status === 'ready' ? 'Tổng hợp lại (Voice → Ảnh → MP4 9:16)' : 'Tạo Voice & Ảnh → Xuất MP4 9:16'}
          </button>
          ${ownerAudio && short.status === 'scripted' ? `<button type="button" class="btn-sm btn-primary" onclick="publishShortJob('${esc(id)}', this)">Phát hành job ngay</button>` : ''}
          ${ownerAudio && ['scripted', 'draft'].includes(short.status) ? `<button type="button" class="btn-sm" onclick="clearShortAudioOwner('${esc(id)}', this)">Bỏ audio owner</button>` : ''}
          ${short.status !== 'ready' ? '<span style="font-size:11.5px;color:var(--fg-muted)">Voice ≈ vài giây · ảnh theo bank/gen · ghép + QC tự động.</span>' : ''}
        </div>
      </div>
    </section>`;

  const publishHtml = short.status === 'ready' ? `
    <section class="final-video-panel" style="margin-bottom:0">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="pill pill-ok">MP4 9:16 SẴN SÀNG</span>
            <span class="pill pill-ghost mono">${Number(short.duration_s || 0).toFixed(1)}s · ${scenes.length ? `${scenes.length} scene` : 'Upload thủ công'}</span>
          </div>
          <div style="font-size:12px;color:var(--fg-muted);margin-top:4px">${scenes.length ? 'Karaoke cháy chữ đã burn trực tiếp vào video.' : 'Video thành phẩm được tải lên trực tiếp.'}</div>
        </div>
        <div class="short-publish-actions">
          <a class="btn btn-primary btn-sm" href="/api/v1/shorts/${esc(id)}/video?download=true" download>Tải MP4</a>
          ${renderYoutubeActionButton(short, 'short', 'btn-primary btn-sm')}
          <button type="button" class="btn-ok btn-sm" onclick="openIgPublishModal('${esc(id)}', 'short')">📲 Instagram</button>
          <button type="button" class="btn-ok btn-sm" onclick="openShortPublishModal('${esc(id)}')">Gói đăng bài</button>
          ${short.ass_asset_id ? `<a class="btn btn-sm" href="/api/v1/shorts/${esc(id)}/subtitles.ass" download>.ASS</a>` : ''}
          ${short.srt_asset_id ? `<a class="btn btn-sm" href="/api/v1/shorts/${esc(id)}/subtitles.srt" download>.SRT</a>` : ''}
        </div>
      </div>
      ${renderTodoYoutubeStatusBanner(short, 'short')}
    </section>` : '';

  // G.13 — Agent Job panel: chỉ cho short EN (job mode cho agent video ngoài)
  const jobStatuses = ['queued', 'rendering'];
  const agentJobHtml = (short.lang || 'vi') === 'en' &&
      ['scripted', 'queued', 'rendering'].includes(short.status) ? `
    <section class="panel" id="shortAgentJobPanel">
      <div class="panel-header">
        <div>
          <h3 class="panel-title">Agent Job — video ngoài gen 9:16</h3>
          <div class="panel-subtitle">Phát hành job → agent tải package (audio + timing + template Breaking News 9:16) → submit clip per scene → hệ thống assemble (karaoke + BGM + QC).</div>
        </div>
        <div class="todo-actions">
          ${short.status === 'scripted' ? `<button type="button" class="btn-primary btn-sm" onclick="publishShortJob('${esc(id)}', this)">Phát hành job</button>` : ''}
          ${jobStatuses.includes(short.status) ? `<button type="button" class="btn-primary btn-sm" onclick="assembleShortJob('${esc(id)}', this)">Assemble final</button>` : ''}
        </div>
      </div>
      ${jobStatuses.includes(short.status) ? `
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
          <span class="pill pill-ghost mono">Job package</span>
          <input type="text" readonly value="/api/v1/shorts/${esc(id)}/job" class="mono" style="flex:1;min-width:220px;font-size:11.5px"/>
          <button type="button" class="btn-xs" onclick="copyShortJobUrl(this)">Copy URL</button>
          <a class="btn-xs" href="/api/v1/shorts/${esc(id)}/job" target="_blank" rel="noopener">Mở JSON</a>
        </div>
        <div style="font-size:11.5px;color:var(--fg-muted)">
          Agent poll: <span class="mono">GET /api/v1/short-jobs?status=queued</span>
          · guide: <a href="/api/v1/short-agent-guide" target="_blank" rel="noopener">AGENT_SHORT_GUIDE.md</a>
          · submit: <span class="mono">POST /api/v1/shorts/${esc(id)}/clips</span> (multipart file + seq_num)
        </div>
      ` : ''}
    </section>` : '';

  const phoneHtml = `
    <section class="panel">
      <div class="panel-header">
        <div><h3 class="panel-title">Khung xem trước 9:16</h3></div>
        ${short.status === 'ready' ? `<button type="button" class="btn-xs btn-primary" onclick="openShortCinema('${esc(id)}')">Xem to toàn màn</button>` : ''}
      </div>
      <div class="phone-frame" id="shortPhoneFrame">
        ${short.status === 'ready'
          ? `<video controls preload="metadata" playsinline src="/api/v1/shorts/${esc(id)}/video"></video>`
          : `<div class="phone-empty">
               <span class="empty-slot-badge">${esc((short.status_label || short.status).toUpperCase())}</span>
               <div>${short.status === 'producing'
                 ? 'Đang tổng hợp: voice → ảnh → ghép + QC. Trang sẽ tự cập nhật khi xong.'
                 : 'Chưa có video. Tạo tự động hoặc upload file MP4 thành phẩm bên dưới.'}</div>
             </div>`}
      </div>
      <div class="short-detail-upload-bar">
        <span style="font-size:11.5px;color:var(--fg-muted)">${short.status === 'ready' ? 'Thay thế bằng file MP4 khác:' : 'Hoặc upload trực tiếp MP4 9:16:'}</span>
        <input type="file" id="shortDetailUploadInput" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" class="hidden" onchange="uploadExistingShortVideo('${esc(id)}', this)"/>
        <button type="button" class="btn-xs btn-primary" onclick="document.getElementById('shortDetailUploadInput').click()">📤 Upload MP4</button>
      </div>
      ${short.audio_asset_id ? `
        <div style="margin-top:10px">
          <div style="font-size:11.5px;font-weight:600;color:var(--fg-muted);margin-bottom:4px">Voice đọc (${esc((short.lang || 'vi').toUpperCase())})</div>
          <audio controls preload="none" src="/api/v1/shorts/${esc(id)}/audio" style="width:100%;height:36px"></audio>
        </div>` : ''}
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h3 class="panel-title">Cảnh thoại &amp; thời lượng (${scenes.length})</h3>
          <div class="panel-subtitle">${hasTimeline ? 'Timeline từ audio thật (mốc giây của từng câu).' : 'Timeline sẽ có sau khi tạo voice.'}</div>
        </div>
      </div>
      ${scenes.length ? `
        <div class="short-scene-list">
          ${scenes.map(sc => `
            <div class="short-scene-row">
              <span class="scene-num">#${String(sc.seq_num).padStart(2, '0')}</span>
              <span class="scene-time">${hasTimeline ? `${Number(sc.start_sec).toFixed(1)}s–${Number(sc.end_sec).toFixed(1)}s` : '—'}</span>
              <span class="scene-text">${esc(sc.text)}</span>
            </div>`).join('')}
        </div>
        ${scenes.some(sc => sc.image_asset_id || sc.bank_used) ? `
          <div style="font-size:11.5px;font-weight:600;color:var(--fg-muted);margin:10px 0 6px">Ảnh scene (dọc)</div>
          <div class="short-img-grid">
            ${scenes.filter(sc => sc.image_asset_id || sc.bank_used).map(sc => `
              <div class="img-card">
                <img loading="lazy" src="/api/v1/shorts/${esc(id)}/images/${Number(sc.seq_num)}/file" alt="Scene #${sc.seq_num}" onerror="this.parentElement.innerHTML='<div class=img-fallback-box>Ảnh lỗi</div>'"/>
              </div>`).join('')}
          </div>` : ''}
      ` : '<div style="font-size:12px;color:var(--fg-muted)">Chưa có scene nào.</div>'}
    </section>`;

  page.innerHTML = `
    <nav class="detail-page-nav" aria-label="Điều hướng Short">
      <div class="detail-nav-left">
        <button type="button" class="btn-primary btn-sm" onclick="goShortStudioPage()">← Short Studio</button>
        <div class="detail-breadcrumb">
          <a href="#/shorts" class="crumb-link" onclick="event.preventDefault();goShortStudioPage()">Short Studio</a>
          <span class="crumb-link">/</span>
          <span class="mono" style="color:var(--accent);font-weight:700">${esc(id)}</span>
          <span>·</span>
          <span class="crumb-title" title="${esc(short.title)}">${esc(short.title)}</span>
        </div>
      </div>
      <div class="detail-job-switcher">
        <span class="pill ${SHORT_STATUS_PILL[short.status] || 'pill-ghost'}">${esc(short.status_label)}</span>
      </div>
    </nav>
    ${publishHtml}
    <div class="shorts-layout">
      <div>${scriptEdHtml}${agentJobHtml}</div>
      <div class="phone-side">${phoneHtml}</div>
    </div>`;
}

async function publishShortJob(shortId, btnEl) {
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang phát hành...'; }
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/publish-job`, { method: 'POST' });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi phát hành job'));
    const job = await r.json();
    shortDetailsCache[shortId] = { ...shortDetailsCache[shortId], status: job.status, status_label: 'Đã phát hành job' };
    shortsLoaded = false;
    openShortDetailPage(shortId);
    showToast(`Job đã phát hành — ${job.scenes?.length || 0} scene, agent có thể poll ngay`, 'ok', 3500);
  } catch (err) {
    showToast(err.message || 'Lỗi phát hành job', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Phát hành job'; }
  }
}

async function assembleShortJob(shortId, btnEl) {
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang ghép...'; }
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/assemble`, { method: 'POST', timeoutMs: 120000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi assemble'));
    openShortDetailPage(shortId);
    showToast('Assemble xong — MP4 9:16 sẵn sàng', 'ok', 3000);
  } catch (err) {
    showToast(err.message || 'Lỗi assemble job', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Assemble final'; }
  }
}

function copyShortJobUrl(btnEl) {
  const input = btnEl?.parentElement?.querySelector('input');
  const val = input?.value || '';
  if (!val) return;
  const done = () => showToast('Đã copy job URL', 'ok', 1800);
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(location.origin + val).then(done,
      () => showToast('Không copy được', 'error'));
  } else {
    input.select();
    document.execCommand('copy');
    done();
  }
}

function countWords(text) {
  return String(text || '').trim() ? String(text).trim().split(/\s+/).length : 0;
}

async function saveShortScript(shortId, btnEl) {
  const beats = Array.from(document.querySelectorAll('.short-beat-ed'))
    .map(ta => ta.value.trim()).filter(Boolean);
  const payload = {
    title: document.getElementById('shortTitleEd')?.value.trim(),
    hook: document.getElementById('shortHookEd')?.value.trim(),
    beats,
    closing: document.getElementById('shortClosingEd')?.value.trim(),
    description: document.getElementById('shortDescEd')?.value.trim(),
    source_note: document.getElementById('shortNoteEd')?.value.trim()
  };
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Đang lưu...'; }
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/script`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Lỗi lưu kịch bản'));
    const short = await r.json();
    shortDetailsCache[shortId] = short;
    shortsLoaded = false;
    renderShortDetailPage(short);
    showToast(`Đã lưu kịch bản (${short.word_count} từ, trong ngân sách)`, 'ok', 2600);
  } catch (err) {
    showToast(err.message || 'Lỗi lưu kịch bản', 'error');
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Lưu chỉnh sửa'; }
  }
}

async function produceShort(shortId, btnEl) {
  const prev = btnEl?.textContent || '';
  if (btnEl) { btnEl.disabled = true; btnEl.innerHTML = '<span class="spinner-ring" style="width:12px;height:12px;border-width:2px;border-top-color:var(--accent-ink)"></span> Đang tổng hợp...'; }
  showToast('Đang tổng hợp Short: voice → ảnh → ghép MP4 9:16 (có thể mất vài phút với ảnh gen)', 'info', 8000);
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/produce`, {
      method: 'POST', timeoutMs: 600000
    });
    if (!r.ok) throw new Error(await extractApiError(r, 'Tổng hợp Short thất bại'));
    const short = await r.json();
    shortDetailsCache[shortId] = short;
    shortsLoaded = false;
    renderShortDetailPage(short);
    showToast(`Xong! MP4 9:16 ${Number(short.duration_s || 0).toFixed(1)}s đã sẵn sàng — kiểm tra rồi tải về đăng`, 'ok', 5200);
  } catch (err) {
    showToast(err.message || 'Tổng hợp Short thất bại', 'error');
    openShortDetailPage(shortId);
  } finally {
    if (btnEl && btnEl.isConnected) { btnEl.disabled = false; btnEl.textContent = prev || 'Tạo Voice & Ảnh → Xuất MP4 9:16'; }
  }
}

async function openShortPublishModal(shortId) {
  openWebModal({
    title: 'Gói đăng bài (Title + Hashtag + Nguồn)',
    subtitle: 'Đang tải gói đăng bài...',
    bodyHtml: renderModalLoadingHtml('Đang tải gói đăng bài...')
  });
  try {
    const r = await apiFetch(`/api/v1/shorts/${encodeURIComponent(shortId)}/publish.md`);
    if (!r.ok) throw new Error(await extractApiError(r, 'Không thể tải gói đăng bài'));
    const text = await r.text();
    lastModalCopyText = text;
    openWebModal({
      title: 'Gói đăng bài (Title + Hashtag + Nguồn)',
      subtitle: 'Sao chép rồi dán lên TikTok / Facebook / YouTube',
      bodyHtml: `<div class="code-viewer">${esc(text)}</div>`,
      footHtml: `<button type="button" class="btn-sm" onclick="copyModalText(this)">Sao chép nội dung</button>`
    });
  } catch (err) {
    openWebModal({
      title: 'Gói đăng bài',
      subtitle: 'Lỗi tải dữ liệu',
      bodyHtml: renderModalErrorHtml(err.message || 'Lỗi tải', `openShortPublishModal('${esc(shortId)}')`)
    });
  }
}

function openShortCinema(shortId) {
  openWebModal({
    title: 'Xem Short 9:16 toàn màn hình',
    bodyHtml: `
      <div class="cinema-video-wrap">
        <video id="cinemaModalVideo" controls autoplay playsinline src="/api/v1/shorts/${esc(shortId)}/video"></video>
      </div>`,
    footHtml: `<span style="font-size:12px;color:var(--ok);font-weight:600">Phát trực tiếp — karaoke đã burn sẵn</span>`
  });
}

// --- Boot ---
