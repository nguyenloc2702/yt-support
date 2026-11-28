// core.js — helpers/api/auth/modal/lazy dung chung (split tu index.html)
let allTodos = [];
const detailsCache = {};
const gridFilterByItem = {};        // itemId -> 'all' | 'missing' | 'has_video' | 'drift'
const gridCollapsedByItem = {};     // itemId -> boolean (true = keep compact 8 shots, false = full auto infinite scroll)
const shotRenderLimitByItem = {};   // itemId -> number of shots currently mounted in DOM
const checklistFilterByItem = {};   // itemId -> 'all' | 'unchecked' | 'checked' | 'drift'
const checklistCollapsedByItem = {}; // itemId -> boolean (true = keep compact 12 rows, false = full auto infinite scroll)
const checklistRenderLimitByItem = {}; // itemId -> number of checklist rows currently mounted in DOM
const uploadedImgLimitByItem = {};  // itemId -> number of uploaded images currently mounted
const refImgLimitByItem = {};       // itemId -> number of reference images currently mounted
const refDetailsOpenByItem = {};    // itemId -> boolean (whether <details> for reference images is open)
const detailFlowTabByItem = {};     // itemId -> 'overview' | 'shots' | 'checklist' | 'assets' | 'all'
const TODO_INFINITE_BATCH_SIZE = 12;
const SHOT_LAZY_BATCH_SIZE = 8;
const CHECKLIST_LAZY_BATCH_SIZE = 12;
const IMG_LAZY_BATCH_SIZE = 12;
const TRANSPARENT_PIXEL = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
let currentFilteredTodos = [];
let renderedTodoCount = 0;
let isAppendingTodoBatch = false;
let todoAppendTimer = null;
let currentStatusFilter = '';
let currentSortOrder = 'default';
let selectedJobId = '';             // '' = Master List Page, or specific item_id
let activeDetailItemId = '';        // '' = on Page 1 (#pageTodoList), or item_id on Page 2 (#pageTodoDetail)
let activeDetailShortId = '';       // short_id when Page Short Studio displays detail
let suppressHashSync = false;
let quickUploadTarget = null;       // { itemId, shotIndex }
let currentModalContext = null;     // For keyboard prev/next in Cinema or Lightbox
let videoObserver = null;
let offscreenVideoObserver = null;
let todoInfiniteObserver = null;
let sectionLazyObserver = null;
let imageLazyObserver = null;
let isLoadingTodos = false;
let lastLoadError = null;

// Nhãn VI cho mọi trạng thái Item (DESIGN.md §3/§4 — cấm hiện enum thô một mình)
const STATUS_LABELS = {
  draft: 'Nháp',
  processing_input: 'Đang nhận tư liệu',
  analyzing: 'Đang phân tích',
  todo: 'Đang chờ gen video',
  awaiting_review: 'Chờ duyệt',
  generating: 'Đang gen',
  held_review: 'Giữ lỗi — chờ duyệt lại',
  assembling: 'Đang ghép video',
  ready: 'Sẵn sàng',
  ready_to_assemble: 'Đủ thời lượng — Sẵn sàng ráp',
  completed: 'Đã hoàn thành',
  assembled: 'Đã ráp thành phẩm',
  exported: 'Đã xuất bản',
  failed: 'Lỗi xử lý',
  paused: 'Tạm dừng',
  cancelled: 'Đã hủy',
  deleted: 'Đã xóa'
};

// Trạng thái phải dùng pill màu --held (held là màu độc quyền của held/failed)
const STATUS_PILL_HELD = new Set(['held_review', 'failed', 'cancelled']);

function statusPillClass(status) {
  if (STATUS_PILL_HELD.has(status)) return 'pill-failed';
  if (status === 'completed' || status === 'assembled' || status === 'exported') return 'pill-assembled';
  if (status === 'ready_to_assemble' || status === 'ready' || status === 'awaiting_review') return 'pill-ready';
  if (['todo', 'generating', 'analyzing', 'assembling', 'processing_input'].includes(status)) return 'pill-todo';
  return 'pill-ghost';
}

// Nhãn VI cho trạng thái góp ý audio (không hiện enum thô)
const FEEDBACK_STATUS_LABELS = {
  open: 'Đang mở',
  resolved: 'Đã xử lý',
  dismissed: 'Đã bỏ qua'
};

const SHOT_TYPE_LABELS = {
  scene: 'Cảnh kể',
  quote: 'Trích dẫn',
  crowd: 'Đám đông',
  diagram: 'Sơ đồ'
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmtSec(v) {
  const n = Number(v || 0);
  return n.toFixed(2) + 's';
}

function fmtDelta(delta, hasVideo = true) {
  if (!hasVideo) return 'Chưa có video';
  const d = Number(delta || 0);
  const sign = d > 0 ? '+' : '';
  return `${sign}${d.toFixed(2)}s`;
}

function getDeltaClass(status) {
  if (status === 'ok') return 'delta-ok';
  if (status === 'overrun') return 'delta-over';
  return 'delta-short';
}

function getDeltaPill(shot) {
  if (!shot.has_video && !shot.checked) {
    return `<span class="pill pill-danger">Thiếu -${Number(shot.expected_duration_sec || 0).toFixed(2)}s</span>`;
  }
  const d = Number(shot.delta_duration_sec || 0);
  const sign = d > 0 ? '+' : '';
  if (Math.abs(d) <= 0.35) {
    return `<span class="pill pill-ok">Khớp chuẩn (${sign}${d.toFixed(2)}s)</span>`;
  }
  if (d > 0.35) {
    return `<span class="pill pill-warn">Dư ${sign}${d.toFixed(2)}s</span>`;
  }
  return `<span class="pill pill-danger">Lệch ${d.toFixed(2)}s</span>`;
}

// --- Auth & API Helper (Session Cookie First, Timeout & Auto-Retry, Zero Raw Clutter) ---
function apiHeaders(extra = {}) {
  const h = { ...extra };
  const customKey = (localStorage.getItem('vs_custom_api_key') || '').trim();
  if (customKey) {
    h['Authorization'] = 'Bearer ' + customKey;
  }
  return h;
}

async function extractApiError(r, fallbackMsg = 'Yêu cầu thất bại') {
  if (!r) return fallbackMsg;
  try {
    const raw = await r.text();
    if (raw) {
      try {
        const d = JSON.parse(raw);
        const msg = d.detail || d.shortfall_warning || d.error || d.message;
        if (msg) {
          return typeof msg === 'string'
            ? `${msg} (HTTP ${r.status})`
            : `${JSON.stringify(msg)} (HTTP ${r.status})`;
        }
      } catch (_) {
        const clean = raw.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
        if (clean) return `${clean} (HTTP ${r.status})`;
      }
    }
  } catch (_) {}
  return `${fallbackMsg} (HTTP ${r.status})`;
}

async function apiFetch(url, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const timeoutMs = opts.timeoutMs || (method === 'GET' ? 25000 : 120000);
  const maxAttempts = (method === 'GET' && !opts._noRetry) ? 2 : 1;
  let lastErr = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const fetchOpts = { ...opts };
      delete fetchOpts.timeoutMs;
      delete fetchOpts._noRetry;
      const merged = {
        credentials: 'same-origin',
        ...fetchOpts,
        headers: apiHeaders(opts.headers || {}),
        ...(ctrl && !opts.signal ? { signal: ctrl.signal } : {})
      };
      const r = await fetch(url, merged);
      if (timer) clearTimeout(timer);
      if (r.status === 401 && !url.includes('/api/v1/auth/')) {
        showLockScreen('Phiên làm việc đã hết hạn — vui lòng nhập lại mã PIN.');
      }
      // Retry once on transient 502/503/504 gateway errors for GET
      if (attempt < maxAttempts && (r.status === 502 || r.status === 503 || r.status === 504)) {
        await new Promise(res => setTimeout(res, 450));
        continue;
      }
      return r;
    } catch (err) {
      if (timer) clearTimeout(timer);
      lastErr = err;
      if (attempt < maxAttempts) {
        await new Promise(res => setTimeout(res, 450));
        continue;
      }
      if (err && err.name === 'AbortError') {
        throw new Error(`Máy chủ phản hồi quá thời gian chờ (${Math.round(timeoutMs / 1000)}s) khi gọi ${url}`);
      }
      throw new Error(`Lỗi kết nối mạng tới máy chủ (${err && err.message ? err.message : 'Network error'})`);
    }
  }
  throw lastErr || new Error('Không thể kết nối máy chủ');
}

// --- UI Feedback: Global Status Banner, Loading Skeletons & Toast Notifications ---
function showStatusBanner({ type = 'error', title = '', detail = '', showRetry = true }) {
  const el = document.getElementById('globalStatusBanner');
  if (!el) return;
  const icon = type === 'error' ? '<span class="status-dot" style="background:var(--held)"></span>'
    : (type === 'loading' ? '<span class="spinner-ring"></span>'
    : (type === 'warn' ? '<span class="status-dot" style="background:var(--run)"></span>'
    : '<span class="status-dot" style="background:var(--ok)"></span>'));
  el.className = `status-banner is-${type}`;
  el.innerHTML = `
    <div class="status-banner-main">
      <div style="margin-top:6px;display:flex">${icon}</div>
      <div>
        <div class="status-banner-title">${esc(title)}</div>
        ${detail ? `<div class="status-banner-detail">${esc(detail)}</div>` : ''}
      </div>
    </div>
    <div class="status-banner-actions">
      ${showRetry ? `
        <button type="button" class="btn-primary btn-xs" onclick="loadTodos({force:true, notify:true})">
          🔄 Thử tải lại ngay
        </button>
      ` : ''}
      <button type="button" class="btn-xs" onclick="hideStatusBanner()">✕ Đóng</button>
    </div>
  `;
}

function hideStatusBanner() {
  const el = document.getElementById('globalStatusBanner');
  if (!el) return;
  el.classList.add('hidden');
  el.innerHTML = '';
}

function showToast(message, type = 'info', durationMs = 4200) {
  const box = document.getElementById('toastContainer');
  if (!box) return;
  const item = document.createElement('div');
  item.className = `toast-item is-${type}`;
  item.innerHTML = `
    <div style="display:flex;align-items:flex-start;gap:8px;line-height:1.35">
      <span>${esc(message)}</span>
    </div>
    <button type="button" class="btn-xs" style="padding:1px 6px;background:rgba(255,255,255,0.15);color:currentColor;border:none" onclick="this.parentElement.remove()">✕</button>
  `;
  box.appendChild(item);
  if (durationMs > 0) {
    setTimeout(() => {
      if (item.isConnected) item.remove();
    }, durationMs);
  }
}

function renderLoadingSkeleton() {
  const dirWrap = document.getElementById('jobDirectoryContainer');
  if (dirWrap && !allTodos.length) {
    const skelCards = Array.from({ length: 6 }).map(() => `
      <div class="skeleton-card">
        <div class="skeleton-line short"></div>
        <div class="skeleton-line med"></div>
        <div class="skeleton-line full"></div>
      </div>
    `).join('');
    dirWrap.innerHTML = `
      <section class="job-directory-panel" id="jobDirectoryPanel">
        <div style="display:flex;align-items:center;gap:10px;font-weight:600;font-size:13px">
          <span class="spinner-ring"></span>
          <span>Đang tải danh mục công việc TODO từ máy chủ...</span>
        </div>
        <div class="skeleton-grid">${skelCards}</div>
      </section>
    `;
  }
  const listWrap = document.getElementById('todoList');
  if (listWrap && !allTodos.length) {
    listWrap.innerHTML = `
      <div class="loading-panel" id="initialLoadingPanel">
        <div style="display:inline-flex;align-items:center;gap:10px;font-size:14px;font-weight:600;color:var(--fg)">
          <span class="spinner-ring lg"></span>
          <span>Đang tải danh sách công việc, tiến độ Video Shot & Bảng Checklist...</span>
        </div>
        <div style="font-size:12px;color:var(--fg-muted);margin-top:6px">
          Vui lòng đợi trong giây lát — hệ thống đang đồng bộ dữ liệu trực tiếp.
        </div>
      </div>
    `;
  }
}

function renderLoadErrorState(errMsg) {
  const dirWrap = document.getElementById('jobDirectoryContainer');
  if (dirWrap && !allTodos.length) {
    dirWrap.innerHTML = '';
  }
  const listWrap = document.getElementById('todoList');
  if (listWrap && !allTodos.length) {
    listWrap.innerHTML = `
      <div class="error-state-panel" id="loadErrorStatePanel">
        <div style="font-size:26px;margin-bottom:6px">⚠️</div>
        <div style="font-size:15px;font-weight:700;margin-bottom:6px">
          Không thể tải dữ liệu danh sách công việc TODO
        </div>
        <div class="mono" style="font-size:12px;background:var(--held-soft);padding:8px 12px;border-radius:var(--r);display:inline-block;max-width:100%;margin-bottom:14px;border:1px solid rgba(239,68,68,.4)">
          Chi tiết lỗi: ${esc(errMsg)}
        </div>
        <div style="font-size:12px;margin-bottom:14px;color:var(--held)">
          Nguyên nhân có thể do kết nối mạng bị gián đoạn tạm thời hoặc phiên đăng nhập đã hết hạn.
        </div>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <button type="button" class="btn-primary" onclick="loadTodos({force:true, notify:true})">
            🔄 Thử tải lại dữ liệu ngay
          </button>
          <button type="button" onclick="showLockScreen('Nhập lại mã PIN để làm mới phiên đăng nhập.')">
            🔑 Đăng nhập lại bằng PIN
          </button>
        </div>
      </div>
    `;
  }
}

window.addEventListener('unhandledrejection', (ev) => {
  const reason = ev && ev.reason;
  const msg = reason && (reason.message || String(reason)) || 'Lỗi bất đồng bộ không xác định';
  console.error('[UnhandledRejection]', reason);
  showToast('Lỗi xử lý: ' + msg, 'error');
});

// A11y: kích hoạt thẻ role="button" (card có onclick) bằng Enter/Space
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const el = (e.target instanceof Element) ? e.target.closest('[role="button"][data-kb-click]') : null;
  if (!el) return;
  e.preventDefault();
  el.click();
});

// --- PIN Gate Logic ---
const pinInputs = () => Array.from(document.querySelectorAll('#pinBoxes input'));

// Danh tính phiên hiện tại (từ /auth/whoami) — null = owner PIN chưa rõ / chưa load
let VS_ME = null;
let AUTH_MODE = 'owner'; // 'owner' (PIN) | 'agent' (mã 6 số)

function setAuthMode(mode) {
  AUTH_MODE = (mode === 'agent') ? 'agent' : 'owner';
  const o = document.getElementById('authModeOwner');
  const a = document.getElementById('authModeAgent');
  if (o) o.classList.toggle('active', AUTH_MODE === 'owner');
  if (a) a.classList.toggle('active', AUTH_MODE === 'agent');
  const sub = document.getElementById('authSubtitle');
  if (sub) {
    sub.textContent = AUTH_MODE === 'owner'
      ? 'Nhập mã PIN 6 chữ số để mở bảng điều phối sản xuất & danh sách công việc TODO'
      : 'Nhập MÃ 6 SỐ của agent (đổi lấy token 30 ngày) — reviewer/agent dùng đường này';
  }
  const boxes = pinInputs();
  boxes.forEach(b => { b.value = ''; b.classList.remove('filled'); });
  document.getElementById('authErr').textContent = '';
  if (boxes[0]) boxes[0].focus();
}

function renderIdentityChip() {
  const chip = document.getElementById('identityChip');
  if (!chip) return;
  if (!VS_ME || !VS_ME.identity) { chip.classList.add('hidden'); chip.innerHTML = ''; return; }
  const initial = String(VS_ME.identity).slice(0, 1).toUpperCase();
  const isOwner = VS_ME.identity === 'owner';
  chip.innerHTML = `<span class="cmt-avatar" style="background:${isOwner ? 'var(--ok)' : 'var(--accent)'}">${esc(initial)}</span>
    <span>${esc(VS_ME.identity)} <span class="rb-scope">· ${esc(VS_ME.scope || '?')}</span></span>`;
  chip.classList.remove('hidden');
}

async function loadWhoami() {
  try {
    const r = await apiFetch('/api/v1/auth/whoami', { timeoutMs: 10000 });
    if (r.ok) {
      VS_ME = await r.json();
      renderIdentityChip();
    } else {
      VS_ME = null;
      renderIdentityChip();
    }
  } catch (err) {
    console.warn('[loadWhoami] Error:', err);
  }
}

function setupPinBoxes() {
  const boxes = pinInputs();
  boxes.forEach((inp, idx) => {
    inp.addEventListener('input', () => {
      inp.value = inp.value.replace(/\D/g, '').slice(-1);
      inp.classList.toggle('filled', inp.value !== '');
      document.getElementById('authErr').textContent = '';
      if (inp.value && idx < boxes.length - 1) {
        boxes[idx + 1].focus();
      } else if (boxes.every(b => b.value.length === 1)) {
        document.getElementById('authBtn').form.requestSubmit();
      }
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !inp.value && idx > 0) {
        boxes[idx - 1].value = '';
        boxes[idx - 1].classList.remove('filled');
        boxes[idx - 1].focus();
        e.preventDefault();
      }
    });
    inp.addEventListener('paste', (e) => {
      const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
      if (!digits) return;
      e.preventDefault();
      digits.split('').forEach((ch, i) => {
        if (boxes[i]) {
          boxes[i].value = ch;
          boxes[i].classList.add('filled');
        }
      });
      if (digits.length === 6) {
        document.getElementById('authBtn').form.requestSubmit();
      } else if (boxes[digits.length]) {
        boxes[digits.length].focus();
      }
    });
  });
}

function showLockScreen(msg = '') {
  const ov = document.getElementById('authOverlay');
  ov.classList.remove('hidden');
  document.getElementById('authErr').textContent = msg;
  const boxes = pinInputs();
  boxes.forEach(b => { b.value = ''; b.classList.remove('filled'); });
  setTimeout(() => boxes[0] && boxes[0].focus(), 40);
}

function hideLockScreen() {
  document.getElementById('authOverlay').classList.add('hidden');
  document.getElementById('logoutBtn').style.display = '';
}

async function submitPin(ev) {
  ev.preventDefault();
  const pin = pinInputs().map(b => b.value).join('');
  if (pin.length !== 6) {
    document.getElementById('authErr').textContent = 'Vui lòng nhập đủ 6 chữ số.';
    return;
  }
  const btn = document.getElementById('authBtn');
  const prevText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Đang xác thực...';
  try {
    const loginBody = AUTH_MODE === 'agent' ? { code: pin } : { pin };
    const r = await apiFetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(loginBody),
      timeoutMs: 15000
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      document.getElementById('authErr').textContent = d.detail || ('Lỗi xác thực HTTP ' + r.status);
      const boxes = pinInputs();
      boxes.forEach(b => { b.value = ''; b.classList.remove('filled'); });
      if (boxes[0]) boxes[0].focus();
      return;
    }
    hideLockScreen();
    await loadWhoami();
    rbRefreshInboxCache();
    await loadTodos({ force: true });
    setTimeout(() => { loadStorageHealth(); }, 150);
  } catch (err) {
    console.error('[submitPin] Error:', err);
    document.getElementById('authErr').textContent =
      (err && err.message) ? err.message : 'Không thể kết nối máy chủ xác thực.';
  } finally {
    btn.disabled = false;
    btn.textContent = prevText || 'Mở Khóa';
  }
}

async function logout() {
  localStorage.removeItem('vs_custom_api_key');
  localStorage.removeItem('apiKey');
  try {
    await apiFetch('/api/v1/auth/logout', { method: 'POST', timeoutMs: 8000 });
  } catch (err) {
    console.warn('[logout] Error:', err);
  } finally {
    VS_ME = null;
    renderIdentityChip();
    document.getElementById('logoutBtn').style.display = 'none';
    document.getElementById('identityChip')?.classList.add('hidden');
    setAuthMode('owner');
    showLockScreen('Đã khóa phiên làm việc.');
  }
}

// --- Storage Status ---
let lastStorageStatus = null;
async function loadStorageHealth() {
  const btn = document.getElementById('storageBadgeBtn');
  try {
    const r = await apiFetch('/api/v1/storage/status', { timeoutMs: 12000 });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không kiểm tra được dung lượng lưu trữ'));
    }
    const d = await r.json();
    lastStorageStatus = d;
    const freeGb = d.disk && d.disk.free_gb != null ? `${d.disk.free_gb} GB trống` : '';
    const gd = d.remote_reachable ? 'GDrive trực tuyến' : 'Lưu cục bộ';
    if (btn) btn.textContent = `☁️ ${gd}${freeGb ? ' · ' + freeGb : ''}`;
  } catch (err) {
    console.warn('[loadStorageHealth] Error:', err);
    if (btn && btn.textContent.includes('Đang kiểm tra')) {
      btn.textContent = '☁️ Lưu Trữ: Cục bộ';
    }
  }
}

async function openStorageStatusModal() {
  openWebModal({
    title: 'Trạng Thái Lưu Trữ & Đồng Bộ Google Drive',
    subtitle: 'Thông tin dung lượng máy chủ và cấu hình lưu trữ tự động',
    bodyHtml: lastStorageStatus
      ? `<div class="code-viewer">${esc(JSON.stringify(lastStorageStatus, null, 2))}</div>`
      : `<div class="loading-panel"><span class="spinner-ring"></span> Đang kiểm tra trạng thái lưu trữ...</div>`
  });
  if (!lastStorageStatus) {
    await loadStorageHealth();
    const bodyEl = document.getElementById('webModalBody');
    if (bodyEl) {
      bodyEl.innerHTML = `<div class="code-viewer">${esc(JSON.stringify(lastStorageStatus || { error: 'Không thể lấy trạng thái lưu trữ' }, null, 2))}</div>`;
    }
  }
}

// --- Filter & Load Todos ---
function openWebModal({ title, subtitle = '', navHtml = '', bodyHtml = '', footHtml = '' }) {
  document.getElementById('webModalTitle').textContent = title || 'Xem Trực Tiếp Trên Web';
  document.getElementById('webModalSubtitle').textContent = subtitle || '';
  document.getElementById('webModalNav').innerHTML = navHtml || '';
  document.getElementById('webModalBody').innerHTML = bodyHtml || '';
  document.getElementById('webModalFoot').innerHTML = footHtml || '';
  document.getElementById('webViewerModal').classList.add('open');
}

function renderModalLoadingHtml(msg = 'Đang tải nội dung trực tiếp từ máy chủ...') {
  return `
    <div class="loading-panel" style="margin:0">
      <div style="display:inline-flex;align-items:center;gap:10px;font-weight:600">
        <span class="spinner-ring"></span>
        <span>${esc(msg)}</span>
      </div>
    </div>
  `;
}

function renderModalErrorHtml(errMsg, retryJs = '') {
  return `
    <div class="error-state-panel" style="margin:0">
      <div style="font-size:20px;margin-bottom:4px">⚠️</div>
      <div style="font-weight:700;margin-bottom:6px">Không thể tải nội dung</div>
      <div class="mono" style="font-size:12px;margin-bottom:12px">${esc(errMsg)}</div>
      ${retryJs ? `<button type="button" class="btn-primary btn-sm" onclick="${retryJs}">🔄 Thử tải lại</button>` : ''}
    </div>
  `;
}

function closeWebModal() {
  const modal = document.getElementById('webViewerModal');
  const bodyEl = document.getElementById('webModalBody');
  if (bodyEl) {
    bodyEl.querySelectorAll('video').forEach(v => {
      try {
        v.pause();
        v.removeAttribute('src');
        v.load();
      } catch (e) {}
    });
    bodyEl.innerHTML = '';
  }
  modal.classList.remove('open');
  currentModalContext = null;
  pumpVideoLoadQueue();
}

window.addEventListener('keydown', (e) => {
  if (!document.getElementById('webViewerModal').classList.contains('open')) return;
  if (e.key === 'Escape') {
    closeWebModal();
  } else if (e.key === 'ArrowLeft' && currentModalContext && currentModalContext.onPrev) {
    currentModalContext.onPrev();
  } else if (e.key === 'ArrowRight' && currentModalContext && currentModalContext.onNext) {
    currentModalContext.onNext();
  }
});

// 1. Cinema Shot Video Player Modal
function renderCinemaShotKpisHtml(itemId, shot) {
  const expected = Number(shot.expected_duration_sec || 0);
  const actual = Number(shot.actual_duration_sec || 0);
  return `
    <div class="kpi-box">
      <div class="k-lbl">Thời Lượng Dự Kiến</div>
      <div class="k-val">${expected.toFixed(2)}s</div>
    </div>
    <div class="kpi-box">
      <div class="k-lbl">Thời Lượng Thực Tế</div>
      <div class="k-val" style="color:var(--accent)">${actual.toFixed(2)}s</div>
    </div>
    <div class="kpi-box">
      <div class="k-lbl">Độ Lệch So Với Dự Kiến</div>
      <div class="k-val">${getDeltaPill(shot)}</div>
    </div>
    <div class="kpi-box">
      <div class="k-lbl">Trạng Thái Tick Agent</div>
      <div class="k-val">
        <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="checkbox" ${shot.checked ? 'checked' : ''} onchange="toggleShotCheck('${esc(itemId)}', ${shot.index}, this.checked)"/>
          <span>${shot.checked ? 'Đã gen xong' : 'Chưa tick'}</span>
        </label>
      </div>
    </div>
  `;
}

function syncCinemaModalShotState(itemId, shotIndex) {
  const item = detailsCache[itemId];
  if (!item) return;
  const shots = (item.shot_checklist && item.shot_checklist.shots) || item.shots || [];
  const shot = shots.find(s => Number(s.index) === Number(shotIndex));
  if (!shot) return;
  const kpiRow = document.getElementById('cinemaShotKpiRow');
  if (kpiRow) {
    kpiRow.innerHTML = renderCinemaShotKpisHtml(itemId, shot);
  }
  const expected = Number(shot.expected_duration_sec || 0);
  const actual = Number(shot.actual_duration_sec || 0);
  const subEl = document.getElementById('webModalSubtitle');
  if (subEl) {
    subEl.textContent = `Mốc Audio: ${Number(shot.audio_start_sec || 0).toFixed(2)}s → ${Number(shot.audio_end_sec || 0).toFixed(2)}s · Dự kiến: ${expected.toFixed(2)}s · Thực tế: ${actual.toFixed(2)}s`;
  }
}

function openCinemaShotModal(itemId, shotIndex) {
  const item = detailsCache[itemId];
  if (!item) return;
  const shots = (item.shot_checklist && item.shot_checklist.shots) || item.shots || [];
  const shot = shots.find(s => Number(s.index) === Number(shotIndex));
  if (!shot) return;

  const prevShot = shots.find(s => Number(s.index) === Number(shotIndex) - 1);
  const nextShot = shots.find(s => Number(s.index) === Number(shotIndex) + 1);

  currentModalContext = {
    type: 'cinema_shot',
    itemId,
    shotIndex: Number(shot.index),
    onPrev: prevShot ? () => openCinemaShotModal(itemId, prevShot.index) : null,
    onNext: nextShot ? () => openCinemaShotModal(itemId, nextShot.index) : null
  };

  const padIdx = String(shot.index).padStart(2, '0');
  const expected = Number(shot.expected_duration_sec || 0);
  const actual = Number(shot.actual_duration_sec || 0);

  openWebModal({
    title: `🎬 Xem Trực Tiếp Shot #${padIdx} — ${item.title}`,
    subtitle: `Mốc Audio: ${Number(shot.audio_start_sec || 0).toFixed(2)}s → ${Number(shot.audio_end_sec || 0).toFixed(2)}s · Dự kiến: ${expected.toFixed(2)}s · Thực tế: ${actual.toFixed(2)}s`,
    navHtml: `
      <button type="button" class="btn-sm" ${!prevShot ? 'disabled' : ''} onclick="openCinemaShotModal('${esc(itemId)}', ${prevShot ? prevShot.index : 1})">◀ Shot trước</button>
      <button type="button" class="btn-sm" ${!nextShot ? 'disabled' : ''} onclick="openCinemaShotModal('${esc(itemId)}', ${nextShot ? nextShot.index : 1})">Shot tiếp ▶</button>
    `,
    bodyHtml: `
      ${shot.has_video && shot.video_url ? `
        <div class="cinema-video-wrap">
          <video id="cinemaModalVideo" controls autoplay playsinline src="${esc(shot.video_url)}"></video>
        </div>
      ` : `
        <div class="panel" style="text-align:center;padding:32px 16px">
          <span class="empty-slot-badge">TRỐNG — SHOT #${padIdx} CHƯA CÓ VIDEO</span>
          <p style="margin:12px 0">Shot này cần gen đoạn video dài <b>${expected.toFixed(2)} giây</b>.</p>
          <button type="button" class="btn-primary" onclick="closeWebModal();triggerQuickSlotUpload('${esc(itemId)}', ${shot.index})">
            + Tải video vào Shot #${padIdx}
          </button>
        </div>
      `}
      <div id="cinemaShotKpiRow" class="cinema-kpi-grid">
        ${renderCinemaShotKpisHtml(itemId, shot)}
      </div>
      <div style="margin-top:12px;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--r)">
        <div style="font-size:11px;font-weight:600;color:var(--fg-muted);text-transform:uppercase">Lời thoại tiếng Anh (Narration)</div>
        <div style="font-size:14px;font-weight:500;margin-top:4px">${esc(shot.narration)}</div>
        ${shot.visual_prompt ? `
          <div style="font-size:11px;font-weight:600;color:var(--fg-muted);text-transform:uppercase;margin-top:10px">Gợi ý hình ảnh (Visual Prompt)</div>
          <div class="mono" style="font-size:12px;color:var(--fg-muted);margin-top:2px">${esc(shot.visual_prompt)}</div>
        ` : ''}
      </div>
    `,
    footHtml: `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:12px;color:var(--fg-muted)">Tốc độ phát:</span>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(0.75)">0.75x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.0)">1.0x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.25)">1.25x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.5)">1.5x</button>
      </div>
      <div style="font-size:12px;color:var(--fg-muted)">Mẹo: Dùng phím ◀ / ▶ để chuyển nhanh giữa các Shot</div>
    `
  });
}

function setCinemaSpeed(rate) {
  const v = document.getElementById('cinemaModalVideo');
  if (v) v.playbackRate = rate;
}

function openCinemaFinalVideo(itemId) {
  const item = detailsCache[itemId] || allTodos.find(x => x.item_id === itemId);
  if (!item || !item.final_video_url) return;
  openWebModal({
    title: `🎉 Video Thành Phẩm Hoàn Chỉnh — ${item.title}`,
    subtitle: `Tổng thời lượng chuẩn: ${fmtSec(item.audio_duration_sec)}`,
    bodyHtml: `
      <div class="cinema-video-wrap">
        <video id="cinemaModalVideo" controls autoplay playsinline src="${esc(item.final_video_url)}"></video>
      </div>
    `,
    footHtml: `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span style="font-size:12px;color:var(--fg-muted)">Tốc độ phát:</span>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(0.75)">0.75x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.0)">1.0x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.25)">1.25x</button>
        <button type="button" class="btn-xs" onclick="setCinemaSpeed(1.5)">1.5x</button>
      </div>
      <span style="font-size:12px;color:var(--ok);font-weight:600">Đang phát trực tiếp trên trình duyệt</span>
    `
  });
}

// 2. Gallery Lightbox Modal
function openGalleryLightbox(itemId, group, index) {
  const item = detailsCache[itemId];
  if (!item) return;
  const list = group === 'uploaded' ? (item.uploaded_images || []) : (item.reference_images || []);
  const img = list[index];
  if (!img) return;

  currentModalContext = {
    onPrev: index > 0 ? () => openGalleryLightbox(itemId, group, index - 1) : null,
    onNext: index < list.length - 1 ? () => openGalleryLightbox(itemId, group, index + 1) : null
  };

  const title = group === 'uploaded'
    ? `🖼 Ảnh Keyframe #${img.seq_num} ${img.shot_index ? `(Shot #${img.shot_index})` : ''}`
    : `🔎 Ảnh Tư Liệu Thực Tế — Shot #${img.shot_index}${img.timestamp_sec != null ? ` (${fmtSec(img.timestamp_sec)})` : ''}`;

  openWebModal({
    title,
    subtitle: `${index + 1} / ${list.length} · Kích thước: ${img.width || '?'}×${img.height || '?'}`,
    navHtml: `
      <button type="button" class="btn-sm" ${index <= 0 ? 'disabled' : ''} onclick="openGalleryLightbox('${esc(itemId)}', '${esc(group)}', ${index - 1})">◀ Ảnh trước</button>
      <button type="button" class="btn-sm" ${index >= list.length - 1 ? 'disabled' : ''} onclick="openGalleryLightbox('${esc(itemId)}', '${esc(group)}', ${index + 1})">Ảnh tiếp ▶</button>
    `,
    bodyHtml: `
      <div class="cinema-img-wrap">
        <img src="${esc(img.file_url)}" alt="${esc(title)}" onerror="handleImgError(this)"/>
      </div>
      <div style="margin-top:10px;font-size:12px;color:var(--fg-muted)">
        ${esc(img.prompt_used || img.narration_snippet || img.search_query || img.name || img.note || '')}
      </div>
    `
  });
}

function openSingleImageModal(url, title, caption) {
  openWebModal({
    title: title || 'Xem Ảnh Trực Tiếp',
    bodyHtml: `
      <div class="cinema-img-wrap">
        <img src="${esc(url)}" alt="${esc(title)}" onerror="handleImgError(this)"/>
      </div>
      ${caption ? `<div style="margin-top:10px;font-size:13px">${esc(caption)}</div>` : ''}
    `
  });
}

// 3. On-Screen Script, Subtitle, Checklist & Guide Viewers (No File Download Needed)
async function openScriptViewerModal(itemId) {
  openWebModal({
    title: `📄 Kịch Bản & Phân Cảnh Chi Tiết (${itemId})`,
    subtitle: 'Đang tải kịch bản...',
    bodyHtml: renderModalLoadingHtml('Đang tải dữ liệu kịch bản và phân cảnh...')
  });
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/script`);
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không thể tải kịch bản'));
    }
    const data = await r.json();
    const shots = (data.shot_checklist && data.shot_checklist.shots) || data.shots || [];
    openWebModal({
      title: `📄 Kịch Bản & Phân Cảnh Chi Tiết — ${data.title || itemId}`,
      subtitle: `Tổng cộng ${shots.length} shot · Xem trực tiếp trên trình duyệt`,
      bodyHtml: `
        <div class="checklist-table-wrap" style="max-height:58vh">
          <table class="checklist-table">
            <thead>
              <tr>
                <th>Shot</th>
                <th>Kiểu</th>
                <th>Mốc Audio</th>
                <th>Dự Kiến</th>
                <th>Thực Tế</th>
                <th>Độ Lệch</th>
                <th>Lời Thoại Tiếng Anh</th>
              </tr>
            </thead>
            <tbody>
              ${shots.map(s => `
                <tr>
                  <td data-label="Vị trí Shot" class="mono"><b>#${String(s.index).padStart(2, '0')}</b></td>
                  <td data-label="Kiểu cảnh">${esc(SHOT_TYPE_LABELS[s.shot_type] || s.shot_type)}</td>
                  <td data-label="Mốc Audio" class="mono">${Number(s.audio_start_sec || 0).toFixed(2)}s → ${Number(s.audio_end_sec || 0).toFixed(2)}s</td>
                  <td data-label="Dự kiến" class="mono">${Number(s.expected_duration_sec ?? s.duration_sec ?? 0).toFixed(2)}s</td>
                  <td data-label="Thực tế" class="mono">${Number(s.actual_duration_sec || 0).toFixed(2)}s</td>
                  <td data-label="Độ lệch">${getDeltaPill(s)}</td>
                  <td data-label="Lời thoại EN" class="td-narration-cell">${esc(s.narration)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `
    });
  } catch (err) {
    console.error('[openScriptViewerModal] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi tải kịch bản';
    openWebModal({
      title: `📄 Kịch Bản & Phân Cảnh Chi Tiết (${itemId})`,
      subtitle: 'Lỗi tải kịch bản',
      bodyHtml: renderModalErrorHtml(msg, `openScriptViewerModal('${esc(itemId)}')`)
    });
  }
}

async function openTextAssetModal(itemId, endpointSuffix, label) {
  openWebModal({
    title: `${label} — Xem Trực Tiếp Trên Web`,
    subtitle: 'Đang tải nội dung...',
    bodyHtml: renderModalLoadingHtml(`Đang tải ${label}...`)
  });
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/${endpointSuffix}`);
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Không thể mở ${label}`));
    }
    const text = await r.text();
    lastModalCopyText = text;
    openWebModal({
      title: `${label} — Xem Trực Tiếp Trên Web`,
      subtitle: `Nội dung hiển thị trực tiếp không cần tải file về máy`,
      bodyHtml: `<div class="code-viewer">${esc(text)}</div>`,
      footHtml: `<button type="button" class="btn-sm" onclick="copyModalText(this)">📋 Sao chép nội dung</button>`
    });
  } catch (err) {
    console.error('[openTextAssetModal] Error:', err);
    const msg = (err && err.message) ? err.message : `Không thể mở ${label}`;
    openWebModal({
      title: `${label} — Xem Trực Tiếp Trên Web`,
      subtitle: 'Lỗi tải tài liệu',
      bodyHtml: renderModalErrorHtml(msg, `openTextAssetModal('${esc(itemId)}', '${esc(endpointSuffix)}', '${esc(label)}')`)
    });
  }
}

async function openChecklistJsonModal(itemId) {
  openWebModal({
    title: `☑️ Dữ Liệu Bảng Tick Chọn Shot Cho Agent (${itemId})`,
    subtitle: 'Đang tải dữ liệu Checklist...',
    bodyHtml: renderModalLoadingHtml('Đang tải dữ liệu bảng Checklist...')
  });
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shot-checklist`);
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không thể tải dữ liệu Checklist'));
    }
    const data = await r.json();
    const pretty = JSON.stringify(data, null, 2);
    lastModalCopyText = pretty;
    openWebModal({
      title: `☑️ Dữ Liệu Bảng Tick Chọn Shot Cho Agent (${itemId})`,
      subtitle: `Đã gen: ${data.checked_shots_count}/${data.total_shots} shot · Cần gen thêm: +${fmtSec(data.remaining_duration_to_generate_sec)}`,
      bodyHtml: `<div class="code-viewer">${esc(pretty)}</div>`,
      footHtml: `<button type="button" class="btn-sm" onclick="copyModalText(this)">📋 Sao chép JSON</button>`
    });
  } catch (err) {
    console.error('[openChecklistJsonModal] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi tải dữ liệu Checklist';
    openWebModal({
      title: `☑️ Dữ Liệu Bảng Tick Chọn Shot Cho Agent (${itemId})`,
      subtitle: 'Lỗi tải dữ liệu',
      bodyHtml: renderModalErrorHtml(msg, `openChecklistJsonModal('${esc(itemId)}')`)
    });
  }
}

async function openSkillViewerModal(itemId) {
  openWebModal({
    title: `📐 Khuôn Mẫu Crayon Vector v2 & Quy Tắc Chuyển Động 30-40-30`,
    subtitle: 'Đang tải khuôn mẫu...',
    bodyHtml: renderModalLoadingHtml('Đang tải bộ quy tắc tạo ảnh/video...')
  });
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/skills`);
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không thể tải khuôn mẫu Skill'));
    }
    const data = await r.json();
    const pretty = JSON.stringify(data, null, 2);
    lastModalCopyText = pretty;
    openWebModal({
      title: `📐 Khuôn Mẫu Crayon Vector v2 & Quy Tắc Chuyển Động 30-40-30`,
      subtitle: `Xem trực tiếp bộ quy tắc tạo ảnh/video của Agent`,
      bodyHtml: `<div class="code-viewer">${esc(pretty)}</div>`,
      footHtml: `<button type="button" class="btn-sm" onclick="copyModalText(this)">📋 Sao chép JSON</button>`
    });
  } catch (err) {
    console.error('[openSkillViewerModal] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi tải khuôn mẫu Skill';
    openWebModal({
      title: `📐 Khuôn Mẫu Crayon Vector v2 & Quy Tắc Chuyển Động 30-40-30`,
      subtitle: 'Lỗi tải dữ liệu',
      bodyHtml: renderModalErrorHtml(msg, `openSkillViewerModal('${esc(itemId)}')`)
    });
  }
}

async function openAgentGuideModal() {
  openWebModal({
    title: '📖 Hướng Dẫn Tích Hợp Dành Cho Agent Sản Xuất Video',
    subtitle: 'Đang tải tài liệu hướng dẫn...',
    bodyHtml: renderModalLoadingHtml('Đang tải hướng dẫn tích hợp Agent...')
  });
  try {
    const r = await apiFetch('/api/v1/agent-guide?format=raw');
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không tải được hướng dẫn Agent'));
    }
    const text = await r.text();
    lastModalCopyText = text;
    openWebModal({
      title: '📖 Hướng Dẫn Tích Hợp Dành Cho Agent Sản Xuất Video',
      subtitle: 'Tài liệu đọc trực tiếp trên màn hình Web',
      bodyHtml: `<div class="code-viewer">${esc(text)}</div>`,
      footHtml: `<button type="button" class="btn-sm" onclick="copyModalText(this)">📋 Sao chép nội dung</button>`
    });
  } catch (err) {
    console.error('[openAgentGuideModal] Error:', err);
    const msg = (err && err.message) ? err.message : 'Không tải được hướng dẫn Agent';
    openWebModal({
      title: '📖 Hướng Dẫn Tích Hợp Dành Cho Agent Sản Xuất Video',
      subtitle: 'Lỗi tải tài liệu',
      bodyHtml: renderModalErrorHtml(msg, `openAgentGuideModal()`)
    });
  }
}

// --- Short Studio (Phụ lục G): Tin nóng & Giải mã tiền bạc, video dọc 9:16 ---
function setActiveNav(page) {
  // MPA: nav là <a data-nav> — active theo trang hiện tại
  document.querySelectorAll('.top-nav-btn[data-nav]').forEach(a => {
    a.classList.toggle('active', a.dataset.nav === page);
  });
}

function hideShortsPage() {
  activeDetailShortId = '';
  const el = document.getElementById('pageShortStudio');
  if (el) {
    cleanupVideosInContainer(el);
    el.classList.add('hidden');
  }
}

function hideYoutubePage() {
  const el = document.getElementById('pageYoutube');
  if (el) el.classList.add('hidden');
}

function hidePublishPage() {
  const el = document.getElementById('pagePublish');
  if (el) el.classList.add('hidden');
}

// === BẢNG CHAT CHUNG CHO AGENT (#/chat) — thread 1-1, tối đa 2 agent ===
let chatThreadCursor = JSON.parse(localStorage.getItem('chatCursor') || '{}');
let chatOpenThreadId = '';
let chatPollTimer = null;

function hideChatPage() {
  const el = document.getElementById('pageChat');
  if (el) el.classList.add('hidden');
  if (chatPollTimer) { clearInterval(chatPollTimer); chatPollTimer = null; }
}

