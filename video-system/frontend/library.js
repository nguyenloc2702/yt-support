/* Knowledge Library UI — standalone page, dùng session PIN/Bearer của app chính. */
const KB = {
  docs: [],
  sources: [],
  selected: null,
  source: '',
  q: '',
  editingNew: false,
  searchTimer: null,
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function fmtDate(ts) {
  const n = Number(ts || 0);
  if (!n) return '—';
  try { return new Date(n * 1000).toLocaleString('vi-VN'); } catch (_) { return '—'; }
}

function fmtBytes(n) {
  const v = Number(n || 0);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(1)} MB`;
}

function statusLabel(status) {
  return ({
    published: 'Published', draft: 'Draft', reviewed: 'Reviewed',
    canonical: 'Canonical', archived: 'Archived', trashed: 'Trash', missing: 'Missing',
  }[status] || status || '—');
}

function statusClass(status) {
  if (status === 'canonical' || status === 'published') return 'ok';
  if (status === 'draft' || status === 'reviewed') return 'source';
  if (status === 'archived' || status === 'trashed' || status === 'missing') return 'warn';
  return '';
}

function sourceLabel(alias) {
  const hit = KB.sources.find((s) => s.alias === alias);
  return hit ? (hit.category || alias) : (alias || '—');
}

function toast(message, type = '') {
  const box = $('libToast');
  if (!box) return;
  const el = document.createElement('div');
  el.className = `lib-toast ${type}`.trim();
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { if (el.isConnected) el.remove(); }, 4200);
}

async function api(path, opts = {}) {
  const headers = { Accept: 'application/json', ...(opts.headers || {}) };
  if (opts.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { credentials: 'same-origin', ...opts, headers });
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch (_) { data = { detail: raw }; }
  if (response.status === 401) {
    $('libAuth')?.classList.remove('hidden');
    const err = new Error('Phiên đăng nhập đã hết hạn.');
    err.status = 401;
    throw err;
  }
  if (!response.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${response.status}`);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function loadSources() {
  try {
    const data = await api('/api/v1/knowledge/sources');
    KB.sources = data.sources || [];
    renderSources();
  } catch (err) {
    console.error('[knowledge] sources', err);
    if (err.status !== 401) toast(err.message, 'error');
  }
}

function renderSources() {
  const box = $('libSources');
  if (!box) return;
  const total = KB.sources.reduce((sum, s) => sum + Number(s.doc_count || 0), 0);
  const rows = [
    `<button type="button" class="lib-source ${KB.source ? '' : 'active'}" data-source="">
       <span>Tất cả</span><small>${total}</small></button>`,
    ...KB.sources.map((s) => `
      <button type="button" class="lib-source ${KB.source === s.alias ? 'active' : ''}" data-source="${esc(s.alias)}">
        <span>${esc(s.category || s.alias)}</span><small>${Number(s.doc_count || 0)}${s.read_only ? ' · RO' : ' · RW'}</small>
      </button>`),
  ];
  box.innerHTML = rows.join('');
  box.querySelectorAll('[data-source]').forEach((btn) => {
    btn.addEventListener('click', () => {
      KB.source = btn.getAttribute('data-source') || '';
      renderSources();
      loadDocs();
    });
  });
}

async function loadStats() {
  try {
    const data = await api('/api/v1/knowledge/stats');
    const box = $('libStats');
    if (!box) return;
    box.innerHTML = `
      <div class="lib-stat"><span>Tài liệu</span><b>${Number(data.total_docs || 0)}</b></div>
      <div class="lib-stat"><span>Dung lượng</span><b>${fmtBytes(data.total_bytes)}</b></div>
      <div class="lib-stat"><span>Nhóm trùng</span><b>${Number(data.duplicate_groups || 0)}</b></div>`;
  } catch (err) {
    console.error('[knowledge] stats', err);
  }
}

async function loadDocs() {
  const params = new URLSearchParams();
  if (KB.q) params.set('q', KB.q);
  if (KB.source) params.set('source', KB.source);
  const status = $('libStatus')?.value || '';
  if (status) params.set('status', status);
  params.set('limit', '150');
  const count = $('libCount');
  if (count) count.textContent = 'Đang tải...';
  try {
    const data = await api(`/api/v1/knowledge/docs?${params.toString()}`);
    KB.docs = data.docs || [];
    const title = $('libTitle');
    if (title) title.textContent = KB.source ? sourceLabel(KB.source) : 'Tất cả tài liệu';
    if (count) count.textContent = `${Number(data.total || 0)} tài liệu${KB.q ? ` cho “${KB.q}”` : ''}`;
    renderList();
  } catch (err) {
    console.error('[knowledge] docs', err);
    if (err.status !== 401) {
      if (count) count.textContent = 'Không tải được danh sách';
      $('libList').innerHTML = `<div class="lib-empty"><b>Không tải được tài liệu</b>${esc(err.message)}</div>`;
    }
  }
}

function renderList() {
  const box = $('libList');
  if (!box) return;
  if (!KB.docs.length) {
    box.innerHTML = '<div class="lib-empty"><b>Chưa có tài liệu phù hợp</b>Thử xoá bộ lọc hoặc bấm “Đồng bộ index”.</div>';
    return;
  }
  box.innerHTML = KB.docs.map((doc) => `
    <div class="lib-card" data-doc-id="${esc(doc.id)}" role="button" tabindex="0">
      <div class="lib-card-top">
        <div>
          <h3 class="lib-card-title">${esc(doc.title || doc.path)}</h3>
          <div class="lib-card-path">${esc(doc.source)}/${esc(doc.path)}</div>
        </div>
        <span class="lib-badge ${statusClass(doc.status)}">${esc(statusLabel(doc.status))}</span>
      </div>
      ${doc.snippet ? `<div class="lib-card-summary">${esc(doc.snippet)}</div>` : ''}
      <div class="lib-badges">
        <span class="lib-badge source">${esc(sourceLabel(doc.source))}</span>
        ${doc.read_only ? '<span class="lib-badge ro">repo · chỉ đọc</span>' : '<span class="lib-badge ok">library · ghi được</span>'}
        <span class="lib-badge">v${Number(doc.version || 1)}</span>
        <span class="lib-badge">${fmtBytes(doc.size_bytes)}</span>
        ${(doc.tags || []).slice(0, 4).map((tag) => `<span class="lib-badge">#${esc(tag)}</span>`).join('')}
      </div>
    </div>`).join('');
  box.querySelectorAll('[data-doc-id]').forEach((card) => {
    const open = () => openDoc(card.getAttribute('data-doc-id'));
    card.addEventListener('click', open);
    card.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
}

async function openDoc(docId) {
  if (!docId) return;
  try {
    const doc = await api(`/api/v1/knowledge/docs/${encodeURIComponent(docId)}`);
    KB.selected = doc;
    renderReader();
    const url = new URL(window.location.href);
    url.searchParams.set('doc', docId);
    history.replaceState(null, '', url.toString());
  } catch (err) {
    console.error('[knowledge] open', err);
    if (err.status !== 401) toast(err.message, 'error');
  }
}

function renderMarkdown(markdown) {
  if (window.marked && window.DOMPurify) {
    const raw = window.marked.parse(markdown || '', { gfm: true, breaks: false });
    return window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }
  return `<pre>${esc(markdown || '')}</pre>`;
}

function renderReader() {
  const doc = KB.selected;
  if (!doc) return;
  const reader = $('libReader');
  const list = $('libList');
  const toolbar = $('libToolbar');
  if (!reader) return;
  list?.classList.add('hidden');
  toolbar?.classList.add('hidden');
  reader.classList.remove('hidden');
  const canEdit = !doc.read_only && doc.status !== 'trashed';
  const canDelete = !doc.read_only && doc.status !== 'trashed';
  const canRestore = doc.status === 'trashed';
  reader.innerHTML = `
    <div class="lib-reader-head">
      <div>
        <h1>${esc(doc.title || doc.path)}</h1>
        <div class="lib-reader-sub">${esc(doc.source)}/${esc(doc.path)}</div>
      </div>
      <div class="lib-reader-actions">
        <button type="button" class="lib-btn small" onclick="backToList()">← Danh sách</button>
        ${doc.read_only ? '<button type="button" class="lib-btn small" onclick="forkCurrent()">Fork sang library</button>' : ''}
        ${canEdit ? '<button type="button" class="lib-btn small primary" onclick="openEditor(false)">Sửa</button>' : ''}
        ${canRestore ? '<button type="button" class="lib-btn small" onclick="restoreCurrent()">Khôi phục</button>' : ''}
        ${canDelete ? '<button type="button" class="lib-btn small danger" onclick="deleteCurrent()">Xoá</button>' : ''}
        <a class="lib-btn small" href="/api/v1/knowledge/docs/${encodeURIComponent(doc.id)}/raw" download>Tải .md</a>
        <button type="button" class="lib-btn small" onclick="copyPath()">Copy path</button>
      </div>
    </div>
    <div class="lib-meta">
      <span>Nguồn <b>${esc(doc.source)}</b></span>
      <span>Trạng thái <b>${esc(statusLabel(doc.status))}</b></span>
      <span>Version <b>v${Number(doc.version || 1)}</b></span>
      <span>Cập nhật <b>${fmtDate(doc.updated_at)}</b></span>
      <span>Owner <b>${esc(doc.owner || '—')}</b></span>
      ${doc.secret_redacted ? '<span class="lib-badge warn">đã redact secret</span>' : ''}
    </div>
    ${(doc.tags || []).length ? `<div class="lib-badges">${doc.tags.map((t) => `<span class="lib-badge">#${esc(t)}</span>`).join('')}</div>` : ''}
    ${(doc.source_refs || []).length ? `<div class="lib-meta"><span>Nguồn tham chiếu <b>${doc.source_refs.map(esc).join(', ')}</b></span></div>` : ''}
    <div class="lib-markdown" id="libMarkdown">${renderMarkdown(doc.content)}</div>`;
  const markdown = $('libMarkdown');
  if (markdown) {
    markdown.querySelectorAll('a[href]').forEach((a) => {
      const href = a.getAttribute('href') || '';
      if (/^https?:\/\//i.test(href)) {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      }
    });
  }
  renderToc();
}

function renderToc() {
  const box = $('libToc');
  const content = $('libMarkdown');
  if (!box || !content) return;
  const headings = [...content.querySelectorAll('h1,h2,h3')];
  if (!headings.length) {
    box.innerHTML = '<div class="lib-toc-empty">Tài liệu không có heading.</div>';
    return;
  }
  box.innerHTML = headings.map((h, i) => {
    const id = `kb-h-${i}`;
    h.id = id;
    return `<button type="button" class="lib-toc-link ${h.tagName === 'H3' ? 'lvl3' : ''}" data-target="${id}">${esc(h.textContent || '')}</button>`;
  }).join('');
  box.querySelectorAll('[data-target]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.getAttribute('data-target'));
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

function backToList() {
  KB.selected = null;
  $('libReader')?.classList.add('hidden');
  $('libList')?.classList.remove('hidden');
  $('libToolbar')?.classList.remove('hidden');
  const url = new URL(window.location.href);
  url.searchParams.delete('doc');
  history.replaceState(null, '', url.toString());
}

function openEditor(isNew) {
  if (!isNew && (!KB.selected || KB.selected.read_only)) return;
  KB.editingNew = !!isNew;
  const doc = KB.selected || {};
  $('libEditorTitle').textContent = isNew ? 'Tài liệu mới' : 'Sửa tài liệu';
  $('libDocPath').value = isNew ? 'notes/' + new Date().toISOString().slice(0, 10) + '-tong-hop.md' : (doc.path || '');
  $('libDocPath').disabled = !isNew;
  $('libDocTitle').value = isNew ? '' : (doc.title || '');
  $('libDocCategory').value = isNew ? 'Tổng hợp' : (doc.category || '');
  $('libDocTags').value = (doc.tags || []).join(', ');
  $('libDocContent').value = isNew ? '' : (doc.content || '');
  $('libEditorError').classList.add('hidden');
  $('libEditor').classList.remove('hidden');
  setTimeout(() => (isNew ? $('libDocTitle') : $('libDocContent'))?.focus(), 30);
}

function closeEditor() {
  $('libEditor')?.classList.add('hidden');
  $('libEditorError')?.classList.add('hidden');
}

function editorError(message) {
  const box = $('libEditorError');
  if (!box) return;
  box.textContent = message;
  box.classList.remove('hidden');
}

async function saveEditor() {
  const path = $('libDocPath').value.trim();
  const title = $('libDocTitle').value.trim();
  const category = $('libDocCategory').value.trim();
  const tags = $('libDocTags').value.split(',').map((s) => s.trim()).filter(Boolean);
  const content = $('libDocContent').value;
  if (!path || !content.trim()) {
    editorError('Path và nội dung không được để trống.');
    return;
  }
  const btn = $('libSaveBtn');
  btn.disabled = true;
  btn.textContent = 'Đang lưu...';
  try {
    let saved;
    if (KB.editingNew) {
      saved = await api('/api/v1/knowledge/docs', {
        method: 'POST',
        body: JSON.stringify({ source: 'library', path, content, title: title || null, category: category || null, tags }),
      });
    } else {
      saved = await api(`/api/v1/knowledge/docs/${encodeURIComponent(KB.selected.id)}`, {
        method: 'PUT',
        headers: { 'If-Match': KB.selected.etag },
        body: JSON.stringify({ content, title: title || null, category: category || null, tags, note: 'ui edit' }),
      });
    }
    closeEditor();
    toast('Đã lưu tài liệu.', 'ok');
    await Promise.all([loadSources(), loadStats(), loadDocs()]);
    await openDoc(saved.id);
  } catch (err) {
    console.error('[knowledge] save', err);
    if (err.status === 412) {
      editorError('Tài liệu đã bị người khác sửa (hash mismatch). Đóng editor, mở lại để lấy bản mới rồi sửa lại.');
    } else if (err.status === 403) {
      editorError(err.message || 'Bạn không có quyền sửa tài liệu này.');
    } else {
      editorError(err.message || 'Không lưu được tài liệu.');
    }
  } finally {
    btn.disabled = false;
    btn.textContent = 'Lưu tài liệu';
  }
}

async function deleteCurrent() {
  if (!KB.selected) return;
  if (!confirm(`Xoá mềm “${KB.selected.title || KB.selected.path}”? Có thể khôi phục từ Trash.`)) return;
  try {
    await api(`/api/v1/knowledge/docs/${encodeURIComponent(KB.selected.id)}`, { method: 'DELETE' });
    toast('Đã chuyển tài liệu vào Trash.', 'ok');
    backToList();
    await Promise.all([loadSources(), loadStats(), loadDocs()]);
  } catch (err) {
    toast(err.message || 'Không xoá được tài liệu.', 'error');
  }
}

async function restoreCurrent() {
  if (!KB.selected) return;
  try {
    await api(`/api/v1/knowledge/docs/${encodeURIComponent(KB.selected.id)}/restore`, { method: 'POST' });
    toast('Đã khôi phục tài liệu.', 'ok');
    await Promise.all([loadSources(), loadStats(), loadDocs()]);
    await openDoc(KB.selected.id);
  } catch (err) {
    toast(err.message || 'Không khôi phục được tài liệu.', 'error');
  }
}

async function forkCurrent() {
  if (!KB.selected) return;
  try {
    const saved = await api(`/api/v1/knowledge/docs/${encodeURIComponent(KB.selected.id)}/fork`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    toast('Đã tạo bản library từ tài liệu repo.', 'ok');
    await Promise.all([loadSources(), loadStats(), loadDocs()]);
    await openDoc(saved.id);
  } catch (err) {
    toast(err.message || 'Không fork được tài liệu.', 'error');
  }
}

function copyPath() {
  const text = KB.selected ? `${KB.selected.source}/${KB.selected.path}` : '';
  if (!text) return;
  navigator.clipboard?.writeText(text).then(
    () => toast('Đã copy path.', 'ok'),
    () => toast('Không copy được path.', 'error'),
  );
}

async function syncLibrary() {
  const btn = $('libSyncBtn');
  btn.disabled = true;
  btn.textContent = 'Đang đồng bộ...';
  try {
    const result = await api('/api/v1/knowledge/sync', { method: 'POST', body: JSON.stringify({ prune_missing: true }) });
    toast(`Đã quét ${result.scanned || 0} file: +${result.added || 0}, ~${result.updated || 0}, -${result.removed || 0}.`, 'ok');
    await Promise.all([loadSources(), loadStats(), loadDocs()]);
  } catch (err) {
    toast(err.message || 'Đồng bộ thất bại.', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Đồng bộ index';
  }
}

function init() {
  $('libSearch')?.addEventListener('input', (ev) => {
    clearTimeout(KB.searchTimer);
    const value = ev.target.value.trim();
    KB.searchTimer = setTimeout(() => {
      KB.q = value;
      loadDocs();
    }, 250);
  });
  $('libAuth')?.classList.add('hidden');
  loadSources();
  loadStats();
  loadDocs().then(() => {
    const docId = new URLSearchParams(window.location.search).get('doc');
    if (docId) openDoc(docId);
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      if (!$('libEditor')?.classList.contains('hidden')) closeEditor();
      else if (KB.selected) backToList();
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
