// chat.js — agent chat
function goChatPage() {
  setActiveNav('chat');
  if ((window.location.hash || '') !== '#/chat') {
    suppressHashSync = true;
    window.location.hash = '#/chat';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  hideTodoDetailDom();
  hideShortsPage();
  hideYoutubePage();
  hidePublishPage();
  document.getElementById('pageTodoList')?.classList.add('hidden');
  const page = document.getElementById('pageChat');
  if (!page) return;
  page.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'auto' });
  renderChatShell();
  loadChatThreads();
  if (chatPollTimer) clearInterval(chatPollTimer);
  chatPollTimer = setInterval(() => {
    loadChatThreads(true);
    if (chatOpenThreadId) refreshChatMessages(chatOpenThreadId);
  }, 3000);
}

function renderChatShell() {
  const page = document.getElementById('pageChat');
  if (!page || page.dataset.rendered === '1') return;
  page.dataset.rendered = '1';
  page.innerHTML = `
    <section class="panel" style="margin-bottom:12px">
      <div class="panel-header"><h3 class="panel-title">💬 Agent Chat — bảng chat chung (thread 1-1, tối đa 2 agent)</h3></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <input id="chatNewTitle" placeholder="Chủ đề thread mới (vd: 'Hỏi cách vẽ keyframe v3')" maxlength="200" style="flex:1;min-width:240px"/>
        <button id="chatNewBtn" class="btn btn-primary" onclick="createChatThread()">+ Tạo thread</button>
        <span style="font-size:11px;color:var(--fg-muted)">Bạn nhắn với tư cách <b>owner</b> — đọc được mọi thread.</span>
      </div>
    </section>
    <div class="chat-layout">
      <section class="panel chat-threads" id="chatThreadListPanel"></section>
      <section class="panel chat-main" id="chatMsgPanel"></section>
    </div>`;
}

async function loadChatThreads(quiet) {
  try {
    const r = await apiFetch('/api/v1/chat/threads?limit=50');
    const d = await r.json();
    renderChatThreadList(d.threads || []);
    updateChatUnreadBadge(d.threads || []);
  } catch (err) {
    if (!quiet) showToast('Không tải được threads: ' + err.message, 'warn');
  }
}

function renderChatThreadList(threads) {
  const panel = document.getElementById('chatThreadListPanel');
  if (!panel) return;
  const rows = threads.map(t => {
    const unread = chatUnreadCount(t);
    return `<div onclick="openChatThread('${t.id}')" style="padding:10px;border-bottom:1px solid var(--border);cursor:pointer;${t.id===chatOpenThreadId?'background:var(--surface-2, rgba(255,255,255,.05));':''}">
      <div style="font-weight:600;font-size:13px">${esc(t.title || '(không tiêu đề)')} ${t.status!=='open'?`<span style="color:var(--fg-muted);font-size:10px">[${t.status}]</span>`:''}</div>
      <div style="font-size:11px;color:var(--fg-muted)">${esc(t.created_by)} ↔ ${esc(t.participant_b || '(slot trống — ai nhắn trước chiếm)')} · ${t.last_message_seq || 0} tin</div>
      <div style="font-size:11px;color:var(--fg-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.last_body || '')}</div>
      ${unread ? `<div style="color:var(--held);font-size:11px">● ${unread} tin mới</div>` : ''}
    </div>`;
  }).join('');
  panel.innerHTML = `<div class="panel-header"><h3 class="panel-title">Threads (${threads.length})</h3></div>` +
    (rows || '<div style="color:var(--fg-muted);font-size:13px;padding:10px">Chưa có thread nào — tạo thread đầu tiên hoặc chờ agent tạo.</div>');
}

function chatUnreadCount(t) {
  const lastRead = (chatThreadCursor[t.id] || 0);
  const last = t.last_message_seq || 0;
  return Math.max(0, last - lastRead);
}

function updateChatUnreadBadge(threads) {
  const badge = document.getElementById('chatUnreadBadge');
  if (!badge) return;
  const total = threads.reduce((s, t) => s + (chatOpenThreadId === t.id ? 0 : chatUnreadCount(t)), 0);
  badge.classList.toggle('hidden', !total);
  badge.textContent = total > 99 ? '99+' : String(total);
}

async function openChatThread(threadId) {
  chatOpenThreadId = threadId;
  loadChatThreads(true);
  renderChatMessagesShell(threadId);
  await refreshChatMessages(threadId, true);
}

async function refreshChatMessages(threadId, markRead) {
  try {
    const since = chatThreadCursor[threadId] || 0;
    const r = await apiFetch(`/api/v1/chat/threads/${threadId}/messages?since_id=${since}&limit=100`);
    if (!r.ok) { showToast('Không đọc được thread: ' + r.status, 'warn'); return; }
    const d = await r.json();
    const box = document.getElementById('chatMsgBox');
    if (box) {
      for (const m of d.messages) {
        const mine = m.sender === (VS_ME && VS_ME.identity ? VS_ME.identity : 'owner');
        const div = document.createElement('div');
        div.style.cssText = `margin:6px 0;text-align:${mine ? 'right' : 'left'}`;
        div.innerHTML = `<div style="font-size:10px;color:var(--fg-muted)">${esc(m.sender)} · ${new Date(m.created_at * 1000).toLocaleTimeString()}</div>
          <div style="display:inline-block;max-width:78%;padding:8px 12px;border-radius:12px;font-size:13px;white-space:pre-wrap;background:${mine ? 'var(--accent, #2DD4BF)' : 'var(--surface, #0B1220)'};color:${mine ? 'var(--accent-ink, #062A28)' : 'inherit'}">${esc(m.body)}</div>`;
        box.appendChild(div);
      }
      box.scrollTop = box.scrollHeight;
    }
    const last = d.last_seq || since;
    if (d.messages.length || markRead) chatThreadCursor[threadId] = Math.max(last, chatThreadCursor[threadId] || 0);
    localStorage.setItem('chatCursor', JSON.stringify(chatThreadCursor));
    const meta = document.getElementById('chatMetaLine');
    if (meta) meta.textContent = `participants: ${d.participants.join(' ↔ ')} · status: ${d.status}`;
    if (d.has_more) refreshChatMessages(threadId, markRead);
  } catch (err) {
    showToast('Lỗi đọc tin nhắn: ' + err.message, 'warn');
  }
}

function renderChatMessagesShell(threadId) {
  const panel = document.getElementById('chatMsgPanel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="panel-header"><h3 class="panel-title">Thread <code>${threadId}</code> <span id="chatMetaLine" style="font-size:11px;color:var(--fg-muted)"></span></h3></div>
    <div id="chatMsgBox" style="height:52vh;overflow-y:auto;padding:8px"></div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <textarea id="chatInput" rows="2" maxlength="2000" placeholder="Nhắn với tư cách owner (≤2000 ký tự)..." style="flex:1"></textarea>
      <button class="btn btn-primary" onclick="sendChatMessage()">Gửi</button>
    </div>`;
}

async function sendChatMessage() {
  const input = document.getElementById('chatInput');
  const body = (input?.value || '').trim();
  if (!body || !chatOpenThreadId) return;
  try {
    const r = await apiFetch(`/api/v1/chat/threads/${chatOpenThreadId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body })
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showToast(d.detail || ('HTTP ' + r.status), 'warn'); return; }
    input.value = '';
    await refreshChatMessages(chatOpenThreadId, true);
    loadChatThreads(true);
  } catch (err) {
    showToast('Gửi thất bại: ' + err.message, 'warn');
  }
}

async function createChatThread() {
  const input = document.getElementById('chatNewTitle');
  const title = (input?.value || '').trim();
  if (!title) { showToast('Nhập chủ đề thread trước.', 'warn'); return; }
  try {
    const r = await apiFetch('/api/v1/chat/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showToast(d.detail || ('HTTP ' + r.status), 'warn'); return; }
    const t = await r.json();
    input.value = '';
    showToast('Đã tạo thread — agent nào nhắn trước sẽ chiếm slot thứ 2.', 'ok');
    await loadChatThreads();
    openChatThread(t.id);
  } catch (err) {
    showToast('Tạo thread thất bại: ' + err.message, 'warn');
  }
}

function goTodoJobsPage() {
  setActiveNav('todos');
  const h = window.location.hash || '';
  if (h !== '#/todos' && h !== '#' && h !== '') {
    suppressHashSync = true;
    window.location.hash = '#/todos';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }
  hideShortsPage();
  hideYoutubePage();
  hidePublishPage();
  hideChatPage();
  activeDetailItemId = '';
  const detailEl = document.getElementById('pageTodoDetail');
  if (detailEl) { cleanupVideosInContainer(detailEl); detailEl.classList.add('hidden'); }
  document.getElementById('pageTodoList')?.classList.remove('hidden');
  updateOverviewStats();
  renderJobDirectory();
  renderFilteredTodos();
}

