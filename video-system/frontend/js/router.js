// router.js — boot chung + hash sync per page (MPA: moi feature 1 trang).
// Moi trang dat window.VS_PAGE trong HTML; router chi dieu hanh trang do.
// Fix lỗ hổng monolith: reload ở #/chat trước đây rơi về trang todos.

setupPinBoxes();

function vsSyncRoute() {
  const page = window.VS_PAGE || 'todos';
  const h = window.location.hash || '';
  if (page === 'todos') {
    if (h.startsWith('#/todos/')) {
      const m = h.match(/^#\/todos\/([^/?#]+)(?:\?tab=([a-z_]+))?/);
      if (m && (detailsCache[m[1]] || allTodos.find(t => t.item_id === m[1]))) {
        openTodoDetailPage(m[1], m[2] || '');
        return;
      }
    }
    setActiveNav('todos');
    backToTodoListPage();
  } else if (page === 'shorts') {
    const m = h.match(/^#\/shorts\/([^/?#]+)/);
    if (m) { openShortDetailPage(m[1]); return; }
    setActiveNav('shorts');
    openShortStudioPage();
  } else if (page === 'chat') {
    setActiveNav('chat');
    goChatPage();
  } else if (page === 'youtube') {
    setActiveNav('youtube');
    openYoutubePage();
  } else if (page === 'publish') {
    setActiveNav('publish');
    openPublishPage();
  }
}

window.addEventListener('hashchange', () => { if (!suppressHashSync) vsSyncRoute(); });

(async () => {
  try {
    const r = await apiFetch('/api/v1/auth/session', { timeoutMs: 12000 });
    const d = await r.json().catch(() => ({ authenticated: false }));
    if (d.authenticated) {
      hideLockScreen();
      await loadWhoami();
      rbRefreshInboxCache();
      loadStorageHealth();
      // Todo list data chi load tren trang todos; cac trang khong tu load
      // du lieu cua feature khac (moi feature 1 trang).
      if (window.VS_PAGE === 'todos') {
        await loadTodos({ force: true });
      }
      vsSyncRoute();
      setTimeout(() => { loadStorageHealth(); }, 150);
    } else {
      showLockScreen();
    }
  } catch (err) {
    console.error('[Boot] Session check failed:', err);
    const errMsg = (err && err.message) ? err.message : 'Không thể kết nối tới máy chủ';
    showStatusBanner({
      type: 'error',
      title: 'Không thể kiểm tra phiên đăng nhập',
      detail: errMsg,
      showRetry: true
    });
    showLockScreen('Lỗi kết nối máy chủ — vui lòng kiểm tra mạng hoặc nhập mã PIN.');
  }
})();
