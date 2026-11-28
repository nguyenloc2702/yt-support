// todos.js — dieu phoi shot: list + detail 7 tab + review board + comments
// [split] bo: router cu (3997-4036) va boot cu (9532-9562) — thay boi router.js
function getEffectiveStatus(t) {
  const d = detailsCache[t.item_id] || t;
  const vp = d.video_progress || {};
  if (d.status === 'completed' || d.status === 'assembled') return 'assembled';
  if (d.status === 'ready_to_assemble' || (d.status === 'todo' && vp.ready_to_assemble)) return 'ready_to_assemble';
  return 'todo';
}

function syncStatusTabsUi() {
  document.querySelectorAll('#statusTabs .seg-tab').forEach(btn => {
    const isActive = btn.getAttribute('data-status') === currentStatusFilter;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
}

function setStatusFilter(status) {
  currentStatusFilter = status;
  selectedJobId = '';
  syncStatusTabsUi();
  if (activeDetailItemId) {
    backToTodoListPage();
    return;
  }
  renderJobDirectory();
  renderFilteredTodos();
}

function setTodoSortOrder(sortKey) {
  currentSortOrder = sortKey || 'default';
  renderFilteredTodos();
}

const VALID_DETAIL_TABS = ['overview', 'final_video', 'shots', 'checklist', 'images', 'upload_audio', 'review_board'];

function normalizeDetailFlowTab(tabKey) {
  if (!tabKey) return 'overview';
  if (tabKey === 'assets') return 'images';
  if (tabKey === 'all') return 'overview';
  return VALID_DETAIL_TABS.includes(tabKey) ? tabKey : 'overview';
}

function openTodoDetailPage(itemId, initialTab = '') {
  if (!itemId) return;
  const detailPageEl = document.getElementById('pageTodoDetail');
  const listPageEl = document.getElementById('pageTodoList');
  if (detailPageEl) {
    cleanupVideosInContainer(detailPageEl);
  }
  activeDetailItemId = itemId;
  selectedJobId = itemId;
  if (initialTab) {
    detailFlowTabByItem[itemId] = normalizeDetailFlowTab(initialTab);
  } else {
    detailFlowTabByItem[itemId] = normalizeDetailFlowTab(detailFlowTabByItem[itemId] || 'overview');
  }
  const activeTab = detailFlowTabByItem[itemId];

  const desiredHash = `#/todos/${encodeURIComponent(itemId)}?tab=${encodeURIComponent(activeTab)}`;
  if (window.location.hash !== desiredHash) {
    suppressHashSync = true;
    window.location.hash = desiredHash;
    setTimeout(() => { suppressHashSync = false; }, 40);
  }

  if (listPageEl) listPageEl.classList.add('hidden');
  if (detailPageEl) detailPageEl.classList.remove('hidden');
  hideShortsPage();
  hideYoutubePage();
  setActiveNav('todos');

  renderTodoDetailPage(itemId);
  window.scrollTo({ top: 0, behavior: 'auto' });
}

function backToTodoListPage() {
  const prevItemId = activeDetailItemId;
  const detailPageEl = document.getElementById('pageTodoDetail');
  const listPageEl = document.getElementById('pageTodoList');
  if (detailPageEl) {
    cleanupVideosInContainer(detailPageEl);
    detailPageEl.innerHTML = '';
    detailPageEl.classList.add('hidden');
  }
  activeDetailItemId = '';
  selectedJobId = '';

  if (window.location.hash && window.location.hash !== '#/todos' && window.location.hash !== '#') {
    suppressHashSync = true;
    window.location.hash = '#/todos';
    setTimeout(() => { suppressHashSync = false; }, 40);
  }

  if (listPageEl) listPageEl.classList.remove('hidden');
  hideShortsPage();
  hideYoutubePage();
  setActiveNav('todos');
  renderJobDirectory();
  renderFilteredTodos();

  if (prevItemId) {
    const cardEl = document.getElementById('card-' + prevItemId);
    if (cardEl) {
      cardEl.scrollIntoView({ behavior: 'auto', block: 'nearest' });
      return;
    }
  }
  window.scrollTo({ top: 0, behavior: 'auto' });
}

function switchDetailFlowTab(tabKey, itemId = activeDetailItemId) {
  const targetId = itemId || activeDetailItemId;
  if (!targetId) return;
  const normalizedTab = normalizeDetailFlowTab(tabKey);
  detailFlowTabByItem[targetId] = normalizedTab;

  const desiredHash = `#/todos/${encodeURIComponent(targetId)}?tab=${encodeURIComponent(normalizedTab)}`;
  if (window.location.hash !== desiredHash) {
    suppressHashSync = true;
    window.location.hash = desiredHash;
    setTimeout(() => { suppressHashSync = false; }, 40);
  }

  if (activeDetailItemId !== targetId) {
    openTodoDetailPage(targetId, normalizedTab);
    return;
  }

  const detailBodyEl = document.getElementById('detail-' + targetId);
  const tabsWrapEl = document.getElementById('detailFlowTabs-' + targetId);
  const item = detailsCache[targetId] || allTodos.find(t => t.item_id === targetId);
  if (detailBodyEl && tabsWrapEl && item) {
    cleanupVideosInContainer(detailBodyEl);
    tabsWrapEl.querySelectorAll('.detail-flow-tab').forEach(btn => {
      const isMatch = btn.getAttribute('data-flow-tab') === normalizedTab;
      btn.classList.toggle('active', isMatch);
      btn.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });
    detailBodyEl.innerHTML = renderDetailTabPaneHtml(item, normalizedTab);
    if (window.scrollY > 0) {
      window.scrollTo({ top: 0, behavior: 'auto' });
    }
    setupLazyVideoObserver(detailBodyEl);
    return;
  }

  renderTodoDetailPage(targetId);
}

function rerenderActiveDetailTab(itemId) {
  if (activeDetailItemId !== itemId) {
    rerenderSingleCard(itemId);
    return;
  }
  const detailBodyEl = document.getElementById('detail-' + itemId);
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId);
  if (!detailBodyEl || !item) {
    rerenderSingleCard(itemId);
    return;
  }
  const activeTab = normalizeDetailFlowTab(detailFlowTabByItem[itemId] || 'overview');
  cleanupVideosInContainer(detailBodyEl);
  detailBodyEl.innerHTML = renderDetailTabPaneHtml(item, activeTab);
  setupLazyVideoObserver(detailBodyEl);
}

function openUploadTabForShot(itemId, shotIndex) {
  switchDetailFlowTab('upload_audio', itemId);
  setTimeout(() => {
    const seqInput = document.getElementById('upShotNum-' + itemId);
    const covInput = document.getElementById('upCoveredShots-' + itemId);
    const fileInput = document.getElementById('upVideoFile-' + itemId);
    if (seqInput && shotIndex != null) seqInput.value = String(shotIndex);
    if (covInput && shotIndex != null) covInput.value = JSON.stringify([Number(shotIndex)]);
    if (fileInput) {
      try { fileInput.focus(); } catch (_) {}
    }
  }, 40);
}

function openUploadTabForImage(itemId, shotIndex = '') {
  switchDetailFlowTab('upload_audio', itemId);
  setTimeout(() => {
    const shotInput = document.getElementById('upImgShotNum-' + itemId);
    const fileInput = document.getElementById('upImgFile-' + itemId);
    if (shotInput && shotIndex !== '' && shotIndex != null) shotInput.value = String(shotIndex);
    if (fileInput) {
      try { fileInput.focus(); } catch (_) {}
    }
  }, 40);
}

function selectSingleJob(itemId) {
  if (!itemId) {
    backToTodoListPage();
    return;
  }
  openTodoDetailPage(itemId);
}

function jumpToJobCard(itemId, ev) {
  if (ev) ev.stopPropagation();
  if (!itemId) return;
  openTodoDetailPage(itemId);
}

async function loadTodos(opts = {}) {
  if (isLoadingTodos && !opts.force) return;
  isLoadingTodos = true;
  lastLoadError = null;

  const refreshBtn = document.getElementById('refreshAllBtn');
  if (refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="spinner-ring" style="width:12px;height:12px;border-width:2px;border-top-color:#fff"></span> Đang tải dữ liệu...';
  }
  const subEl = document.getElementById('statTotalJobsSub');
  if (subEl && !allTodos.length) {
    subEl.textContent = '⏳ Đang tải danh sách từ máy chủ...';
  }

  if (!allTodos.length) {
    renderLoadingSkeleton();
  } else if (opts.notify) {
    showStatusBanner({
      type: 'loading',
      title: 'Đang đồng bộ dữ liệu mới nhất từ máy chủ...',
      detail: 'Vui lòng chờ trong giây lát.',
      showRetry: false
    });
  }

  try {
    const [r, sr] = await Promise.all([
      apiFetch('/api/v1/todos', { timeoutMs: 25000 }),
      apiFetch('/api/v1/shorts', { timeoutMs: 20000 }).catch(() => null),
    ]);
    if (!r.ok) {
      if (r.status === 401) {
        return;
      }
      throw new Error(await extractApiError(r, 'Không thể tải danh sách công việc TODO'));
    }
    const data = await r.json();
    if (!data || !Array.isArray(data.todos)) {
      throw new Error('Dữ liệu trả về từ /api/v1/todos không đúng định dạng (thiếu mảng todos).');
    }

    allTodos = data.todos;

    // Populate detailsCache immediately for ALL items from the unified /api/v1/todos payload
    allTodos.forEach(t => {
      detailsCache[t.item_id] = t;
    });

    if (sr && sr.ok) {
      try {
        const sData = await sr.json();
        if (sData && Array.isArray(sData.shorts)) {
          allShorts = sData.shorts;
          shortsLoaded = true;
          allShorts.forEach(s => { shortDetailsCache[s.id] = s; });
        }
      } catch (_) {}
    }

    hideStatusBanner();
    updateOverviewStats();
    renderJobDirectory();
    renderFilteredTodos();

    if (activeDetailItemId && detailsCache[activeDetailItemId]) {
      renderTodoDetailPage(activeDetailItemId);
    } else {
      vsSyncRoute();  // router.js (MPA)
    }

    if (opts.notify) {
      const sNote = allShorts.length ? ` · ${allShorts.length} Short video` : '';
      showToast(`Đã tải thành công ${allTodos.length} công việc TODO${sNote}`, 'ok', 2800);
    }
  } catch (err) {
    console.error('[loadTodos] Error:', err);
    const errMsg = (err && err.message) ? err.message : String(err || 'Lỗi không xác định khi tải dữ liệu');
    lastLoadError = errMsg;
    if (subEl) {
      subEl.textContent = '⚠️ Lỗi tải dữ liệu — bấm Làm mới';
    }
    showStatusBanner({
      type: 'error',
      title: 'Không thể tải dữ liệu từ máy chủ',
      detail: errMsg,
      showRetry: true
    });
    renderLoadErrorState(errMsg);
  } finally {
    isLoadingTodos = false;
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.textContent = '🔄 Làm mới dữ liệu';
    }
  }
}

function updateOverviewStats() {
  let totalShots = 0;
  let checkedShots = 0;
  let expectedSec = 0;
  let generatedSec = 0;
  let remainingSec = 0;
  let todoCount = 0;
  let readyCount = 0;
  let assembledCount = 0;

  allTodos.forEach(t => {
    const d = detailsCache[t.item_id] || t;
    const eff = getEffectiveStatus(d);
    if (eff === 'assembled') assembledCount++;
    else if (eff === 'ready_to_assemble') readyCount++;
    else todoCount++;

    const cl = d.shot_checklist;
    if (cl) {
      totalShots += Number(cl.total_shots || 0);
      checkedShots += Number(cl.checked_shots_count || 0);
      expectedSec += Number(cl.expected_total_duration_sec || 0);
      generatedSec += Number(cl.generated_total_duration_sec || 0);
      remainingSec += Number(cl.remaining_duration_to_generate_sec || 0);
    } else {
      const vp = d.video_progress || {};
      totalShots += Number(d.total_shots || 0);
      checkedShots += Number(vp.uploaded_videos_count || 0);
      expectedSec += Number(d.audio_duration_sec || 0);
      generatedSec += Number(vp.uploaded_videos_duration_sec || 0);
      remainingSec += Number(vp.shortfall_sec || 0);
    }
  });

  const shortsTotal = allShorts.length;
  const shortsReady = allShorts.filter(s => s.status === 'ready').length;
  document.getElementById('statTotalJobs').textContent = String(allTodos.length + shortsTotal);
  document.getElementById('statTotalJobsSub').textContent =
    `${todoCount} đang gen · ${readyCount} chờ ráp · ${assembledCount} đã ráp` +
    (shortsTotal > 0 ? ` · ${shortsTotal} Short (${shortsReady} sẵn sàng)` : '');
  document.getElementById('statShotsChecked').textContent = `${checkedShots} / ${totalShots}`;
  document.getElementById('statShotsCheckedSub').textContent =
    `Còn ${Math.max(0, totalShots - checkedShots)} shot chưa gen`;
  document.getElementById('statDurationGen').textContent =
    `${generatedSec.toFixed(1)}s / ${expectedSec.toFixed(1)}s`;
  document.getElementById('statRemainingSec').textContent = `+${remainingSec.toFixed(1)}s`;
  document.getElementById('statRemainingSec').style.color =
    remainingSec <= 0.5 ? 'var(--ok)' : 'var(--held)';

  const tabAll = document.getElementById('tabAll');
  const tabTodo = document.getElementById('tabTodo');
  const tabReady = document.getElementById('tabReady');
  const tabAssembled = document.getElementById('tabAssembled');
  const tabShorts = document.getElementById('tabShorts');
  if (tabAll) tabAll.textContent = `Tất cả (${allTodos.length + shortsTotal})`;
  if (tabTodo) tabTodo.textContent = `Đang chờ video (${todoCount})`;
  if (tabReady) tabReady.textContent = `Đủ thời lượng (${readyCount})`;
  if (tabAssembled) tabAssembled.textContent = `Đã ráp thành phẩm (${assembledCount + shortsReady})`;
  if (tabShorts) tabShorts.textContent = `📱 Short Video (${shortsTotal})`;
}

function renderJobDirectory() {
  const wrap = document.getElementById('jobDirectoryContainer');
  if (!wrap) return;
  if (!allTodos.length) {
    wrap.innerHTML = '';
    return;
  }

  const quickPillsHtml = allTodos.map((t, idx) => {
    const d = detailsCache[t.item_id] || t;
    const cl = d.shot_checklist || {};
    const vp = d.video_progress || {};
    const totalShots = Number(cl.total_shots ?? d.total_shots ?? 0);
    const checkedShots = Number(cl.checked_shots_count ?? vp.uploaded_videos_count ?? 0);
    const numStr = String(idx + 1).padStart(2, '0');
    const eff = getEffectiveStatus(d);
    const badgeClass = eff === 'assembled' ? 'btn-ok' : (checkedShots > 0 ? 'btn-primary' : '');
    return `
      <button type="button" class="btn-xs ${badgeClass}" id="dir-card-${esc(d.item_id)}" onclick="openTodoDetailPage('${esc(d.item_id)}')" title="${esc(d.title)} (${checkedShots}/${totalShots} shot)">
        #${numStr} (${checkedShots}/${totalShots})
      </button>`;
  }).join('');

  wrap.innerHTML = `
  <section class="job-directory-panel" id="jobDirectoryPanel" style="padding:11px 14px;margin-bottom:16px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px">
        <span style="font-weight:700;color:var(--fg)">🗂️ Luồng Điều Phối 2 Trang (${allTodos.length} Jobs):</span>
        <span class="pill pill-accent">Trang 1: Danh sách TODO gọn nhẹ</span>
        <span style="color:var(--fg-muted)">→ Bấm vào từng công việc để mở</span>
        <span class="pill pill-ok">Trang 2: Chi tiết 6 Tab Lazy Load (Tổng quan · Thành phẩm · Lưới Shot · Checklist · Ảnh · Tải lên)</span>
      </div>
      <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
        <span style="font-size:11px;font-weight:600;color:var(--fg-muted);margin-right:2px">Mở nhanh:</span>
        ${quickPillsHtml}
      </div>
    </div>
  </section>`;
}

function scrollToSection(sectionId) {
  const el = document.getElementById(sectionId);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function scrollToJobDirectory() {
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function getFilteredShotsForItem(itemId) {
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId) || {};
  const cl = item.shot_checklist;
  const shots = (cl && Array.isArray(cl.shots) && cl.shots.length) ? cl.shots : (item.shots || []);
  const mode = gridFilterByItem[itemId] || 'all';
  return shots.filter(s => {
    if (mode === 'missing') return !s.has_video;
    if (mode === 'has_video') return !!s.has_video;
    if (mode === 'drift') return s.has_video && Math.abs(Number(s.delta_duration_sec || 0)) > 0.35;
    return true;
  });
}

function getFilteredChecklistRowsForItem(itemId) {
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId) || {};
  const cl = item.shot_checklist;
  const shots = (cl && Array.isArray(cl.shots)) ? cl.shots : (item.shots || []);
  const filterMode = checklistFilterByItem[itemId] || 'all';
  return shots.filter(s => {
    if (filterMode === 'unchecked') return !s.checked;
    if (filterMode === 'checked') return !!s.checked;
    if (filterMode === 'drift') return Math.abs(Number(s.delta_duration_sec || 0)) > 0.35 || !s.has_video;
    return true;
  });
}

function isShotGridCollapsed(itemId, filteredCount) {
  if (gridCollapsedByItem[itemId] !== undefined) {
    return !!gridCollapsedByItem[itemId];
  }
  return allTodos.length > 1 && selectedJobId !== itemId && filteredCount > SHOT_LAZY_BATCH_SIZE;
}

function getShotRenderLimit(itemId, filteredCount) {
  const cur = shotRenderLimitByItem[itemId] || SHOT_LAZY_BATCH_SIZE;
  return Math.min(filteredCount, Math.max(SHOT_LAZY_BATCH_SIZE, cur));
}

function toggleShotGridCollapse(itemId) {
  const filteredShots = getFilteredShotsForItem(itemId);
  const curLimit = getShotRenderLimit(itemId, filteredShots.length);
  if (curLimit < filteredShots.length) {
    gridCollapsedByItem[itemId] = false;
    loadMoreShotsBatch(itemId, SHOT_LAZY_BATCH_SIZE, true);
  } else {
    collapseShotsForItem(itemId);
  }
}

function collapseShotsForItem(itemId) {
  gridCollapsedByItem[itemId] = true;
  shotRenderLimitByItem[itemId] = SHOT_LAZY_BATCH_SIZE;
  rerenderActiveDetailTab(itemId);
}

function loadMoreShotsBatch(itemId, batchSize = SHOT_LAZY_BATCH_SIZE, enableAuto = false) {
  const filteredShots = getFilteredShotsForItem(itemId);
  const oldLimit = getShotRenderLimit(itemId, filteredShots.length);
  if (enableAuto) {
    gridCollapsedByItem[itemId] = false;
  }
  const newLimit = Math.min(filteredShots.length, oldLimit + batchSize);
  shotRenderLimitByItem[itemId] = newLimit;

  const gridEl = document.getElementById('shot-grid-list-' + itemId);
  const sentinelWrap = document.getElementById('shot-sentinel-wrap-' + itemId);
  const headerBtn = document.getElementById('shot-collapse-btn-' + itemId);

  if (!gridEl || newLimit <= oldLimit) {
    rerenderActiveDetailTab(itemId);
    return;
  }

  const nextBatch = filteredShots.slice(oldLimit, newLimit);
  const html = nextBatch.map(s => renderShotSlotCard(itemId, s)).join('');
  gridEl.insertAdjacentHTML('beforeend', html);

  if (headerBtn) {
    headerBtn.textContent = newLimit < filteredShots.length
      ? `🔼 Tải thêm (${newLimit}/${filteredShots.length} Shot)`
      : `🔽 Thu gọn (8/${filteredShots.length})`;
  }
  if (sentinelWrap) {
    sentinelWrap.innerHTML = renderShotGridSentinelHtml(itemId, newLimit, filteredShots.length);
  }
  setupLazyVideoObserver(gridEl);
  setupSectionLazyObserver(sentinelWrap || document.getElementById('shot-grid-panel-' + itemId));
}

function renderShotGridSentinelHtml(itemId, visibleCount, totalCount) {
  if (visibleCount >= totalCount) {
    if (totalCount <= SHOT_LAZY_BATCH_SIZE) return '';
    return `
      <div class="infinite-done-banner">
        ✅ Đã hiển thị đầy đủ ${totalCount} / ${totalCount} Shot —
        <button type="button" class="btn-xs" style="margin-left:8px" onclick="collapseShotsForItem('${esc(itemId)}')">🔽 Thu gọn về 8 Shot</button>
      </div>`;
  }
  const rem = totalCount - visibleCount;
  const nextStep = Math.min(SHOT_LAZY_BATCH_SIZE, rem);
  // Continuously auto-load batches of 8 shots on scroll unless the user explicitly clicked "Thu gọn"
  const autoScroll = gridCollapsedByItem[itemId] !== true;
  return `
    <div class="lazy-section-sentinel" id="shot-sentinel-${esc(itemId)}" data-lazy-kind="shots" data-item-id="${esc(itemId)}" data-auto="${autoScroll ? '1' : '0'}">
      <div class="infinite-sentinel-status">
        ${autoScroll ? '<span class="spinner-ring"></span>' : '<span style="font-size:15px">⏸</span>'}
        <div>
          <div id="shot-sentinel-status-${esc(itemId)}">
            ${autoScroll
              ? `Đang tự động tải thêm Shot khi cuộn xuống (Đang hiển thị ${visibleCount} / ${totalCount} Shot)...`
              : `Đang thu gọn ${visibleCount} / ${totalCount} Shot (Đã tạm dừng tự cuộn — bấm nút bên phải để tải tiếp)`}
          </div>
          <div class="infinite-sentinel-sub">Chỉ tải các Shot đang xem trên màn hình để trình duyệt luôn mượt mà.</div>
        </div>
      </div>
      <div class="infinite-sentinel-actions">
        <button type="button" class="btn-primary btn-xs" onclick="loadMoreShotsBatch('${esc(itemId)}', ${SHOT_LAZY_BATCH_SIZE}, true)">
          ↓ Tải thêm ${nextStep} Shot & Cuộn tự động (Còn ${rem})
        </button>
        ${visibleCount > SHOT_LAZY_BATCH_SIZE ? `
          <button type="button" class="btn-xs" onclick="collapseShotsForItem('${esc(itemId)}')">
            🔽 Thu gọn (8 Shot)
          </button>
        ` : ''}
      </div>
    </div>`;
}

function isChecklistCollapsed(itemId, rowCount) {
  if (checklistCollapsedByItem[itemId] !== undefined) {
    return !!checklistCollapsedByItem[itemId];
  }
  return allTodos.length > 1 && selectedJobId !== itemId && rowCount > CHECKLIST_LAZY_BATCH_SIZE;
}

function getChecklistRenderLimit(itemId, rowCount) {
  const cur = checklistRenderLimitByItem[itemId] || CHECKLIST_LAZY_BATCH_SIZE;
  return Math.min(rowCount, Math.max(CHECKLIST_LAZY_BATCH_SIZE, cur));
}

function toggleChecklistCollapse(itemId) {
  const rows = getFilteredChecklistRowsForItem(itemId);
  const curLimit = getChecklistRenderLimit(itemId, rows.length);
  if (curLimit < rows.length) {
    checklistCollapsedByItem[itemId] = false;
    loadMoreChecklistRowsBatch(itemId, CHECKLIST_LAZY_BATCH_SIZE, true);
  } else {
    collapseChecklistForItem(itemId);
  }
}

function collapseChecklistForItem(itemId) {
  checklistCollapsedByItem[itemId] = true;
  checklistRenderLimitByItem[itemId] = CHECKLIST_LAZY_BATCH_SIZE;
  rerenderActiveDetailTab(itemId);
}

function renderChecklistRowHtml(itemId, s) {
  const idx = Number(s.index);
  const padIdx = String(idx).padStart(2, '0');
  const expected = Number(s.expected_duration_sec || 0);
  const genSec = Number(s.generated_duration_sec || 0);
  const remSec = Number(s.remaining_duration_sec || 0);
  return `
  <tr class="${s.checked ? 'row-checked' : (!s.has_video ? 'row-missing' : '')}">
    <td data-label="Trạng thái">
      <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-weight:600">
        <input type="checkbox" ${s.checked ? 'checked' : ''} onchange="toggleShotCheck('${esc(itemId)}', ${idx}, this.checked)"/>
        <span>${s.checked ? 'Đã gen' : 'Chưa'}</span>
      </label>
    </td>
    <td data-label="Vị trí Shot" class="mono" style="font-weight:600">#${padIdx}</td>
    <td data-label="Kiểu cảnh"><span class="pill pill-ghost">${esc(SHOT_TYPE_LABELS[s.shot_type] || s.shot_type)}</span></td>
    <td data-label="Mốc Audio" class="mono">
      ${Number(s.audio_start_sec || 0).toFixed(1)}s–${Number(s.audio_end_sec || 0).toFixed(1)}s
    </td>
    <td data-label="Dự kiến" class="mono" style="font-weight:600">${expected.toFixed(2)}s</td>
    <td data-label="Đã gen">
      <div style="display:flex;align-items:center;gap:4px">
        <input type="number" step="0.1" min="0" class="dur-input" value="${genSec.toFixed(2)}"
          onchange="updateShotDuration('${esc(itemId)}', ${idx}, this.value, ${s.checked ? 'true' : 'false'})"
          title="Nhập hoặc chỉnh số giây đã gen cho Shot #${padIdx}"/>
        <span class="mono" style="font-size:11px;color:var(--fg-muted)">s</span>
      </div>
    </td>
    <td data-label="Độ lệch (Δ)">${getDeltaPill(s)}</td>
    <td data-label="Cần gen thêm" class="mono" style="font-weight:600;color:${remSec <= 0.1 ? 'var(--ok)' : 'var(--held)'}">
      ${remSec <= 0.1 ? 'Đủ (0.00s)' : `+${remSec.toFixed(2)}s`}
    </td>
    <td data-label="Lời thoại & Thao tác" class="td-narration-cell">
      <div class="chk-narration-wrap">
        <span class="chk-narration-text" title="${esc(s.narration)}">
          ${esc(s.narration)}
        </span>
        <div class="chk-narration-actions">
          <button type="button" class="btn-xs" onclick="playAudioSegment('${esc(itemId)}', ${Number(s.audio_start_sec || 0)}, ${Number(s.audio_end_sec || 0)}, ${idx})">
            🔊 Nghe
          </button>
          ${s.has_video ? `
            <button type="button" class="btn-xs btn-primary" onclick="openCinemaShotModal('${esc(itemId)}', ${idx})">
              ▶ Xem video
            </button>
          ` : `
            <button type="button" class="btn-xs" onclick="triggerQuickSlotUpload('${esc(itemId)}', ${idx})">
              + Up video
            </button>
          `}
        </div>
      </div>
    </td>
  </tr>`;
}

function renderChecklistSentinelHtml(itemId, visibleCount, totalCount) {
  if (visibleCount >= totalCount) {
    if (totalCount <= CHECKLIST_LAZY_BATCH_SIZE) return '';
    return `
      <div class="infinite-done-banner">
        ✅ Đã hiển thị đầy đủ ${totalCount} / ${totalCount} dòng Checklist —
        <button type="button" class="btn-xs" style="margin-left:8px" onclick="collapseChecklistForItem('${esc(itemId)}')">🔽 Thu gọn về 12 dòng</button>
      </div>`;
  }
  const rem = totalCount - visibleCount;
  const nextStep = Math.min(CHECKLIST_LAZY_BATCH_SIZE, rem);
  // Note: checklist-sentinel-wrap sits outside the fixed-height .checklist-table-wrap scroll box,
  // so checklist infinite scroll is driven by scrolling inside .checklist-table-wrap (data-auto="0" for window observer).
  return `
    <div class="lazy-section-sentinel" id="checklist-sentinel-${esc(itemId)}" data-lazy-kind="checklist" data-item-id="${esc(itemId)}" data-auto="0">
      <div class="infinite-sentinel-status">
        <span class="spinner-ring"></span>
        <span id="checklist-sentinel-status-${esc(itemId)}">
          Đang hiển thị ${visibleCount} / ${totalCount} dòng Checklist — Cuộn xuống trong bảng hoặc bấm để tải thêm (Lazy Load)...
        </span>
      </div>
      <div class="infinite-sentinel-actions">
        <button type="button" class="btn-primary btn-xs" onclick="loadMoreChecklistRowsBatch('${esc(itemId)}', ${CHECKLIST_LAZY_BATCH_SIZE}, true)">
          ↓ Tải thêm ${nextStep} dòng (Còn ${rem})
        </button>
        ${visibleCount > CHECKLIST_LAZY_BATCH_SIZE ? `
          <button type="button" class="btn-xs" onclick="collapseChecklistForItem('${esc(itemId)}')">
            🔽 Thu gọn (12 dòng)
          </button>
        ` : ''}
      </div>
    </div>`;
}

function loadMoreChecklistRowsBatch(itemId, batchSize = CHECKLIST_LAZY_BATCH_SIZE, enableAuto = false) {
  const rows = getFilteredChecklistRowsForItem(itemId);
  const oldLimit = getChecklistRenderLimit(itemId, rows.length);
  checklistCollapsedByItem[itemId] = false;
  const newLimit = Math.min(rows.length, oldLimit + batchSize);
  checklistRenderLimitByItem[itemId] = newLimit;

  const tbody = document.getElementById('checklist-tbody-' + itemId);
  const sentinelWrap = document.getElementById('checklist-sentinel-wrap-' + itemId);
  const headerBtn = document.getElementById('checklist-collapse-btn-' + itemId);

  if (!tbody || newLimit <= oldLimit) {
    rerenderSingleCard(itemId);
    return;
  }

  const nextBatch = rows.slice(oldLimit, newLimit);
  tbody.insertAdjacentHTML('beforeend', nextBatch.map(s => renderChecklistRowHtml(itemId, s)).join(''));

  if (headerBtn) {
    headerBtn.textContent = newLimit < rows.length
      ? `🔼 Tải thêm (${newLimit}/${rows.length} dòng)`
      : `🔽 Thu gọn bảng (12/${rows.length})`;
  }
  if (sentinelWrap) {
    sentinelWrap.innerHTML = renderChecklistSentinelHtml(itemId, newLimit, rows.length);
  }
  setupSectionLazyObserver(sentinelWrap || document.getElementById('checklist-panel-' + itemId));
}

window.addEventListener('scroll', () => {
  const fab = document.getElementById('mobileFabTop');
  if (!fab) return;
  if (window.scrollY > 420) {
    fab.classList.remove('hidden');
  } else {
    fab.classList.add('hidden');
  }
}, { passive: true });

// --- True Lazy Loading for Videos, Images & Section Infinite Scroll ---
let videoLoadQueue = [];
let activeVideoLoads = 0;
const MAX_CONCURRENT_VIDEO_LOADS = 2;

function setVideoOverlayState(v, isLoaded, statusText = '') {
  if (!v) return;
  const stage = v.closest('.slot-stage') || v.closest('.final-video-stage');
  if (!stage) return;
  const ov = stage.querySelector('.video-lazy-overlay');
  if (!ov) return;
  if (isLoaded) {
    ov.classList.add('is-loaded');
  } else {
    ov.classList.remove('is-loaded');
    if (statusText) {
      const txtEl = ov.querySelector('.video-lazy-text');
      if (txtEl) txtEl.textContent = statusText;
    }
  }
}

function onInlineVideoReady(v) {
  setVideoOverlayState(v, true);
}

function onInlineVideoError(v) {
  setVideoOverlayState(v, true);
}

function onInlineVideoPlay(v) {
  if (v && !v.getAttribute('src') && v.getAttribute('data-src')) {
    v.setAttribute('src', v.getAttribute('data-src'));
  }
  setVideoOverlayState(v, true);
}

function pumpVideoLoadQueue() {
  if (document.getElementById('webViewerModal')?.classList.contains('open')) return;
  while (activeVideoLoads < MAX_CONCURRENT_VIDEO_LOADS && videoLoadQueue.length > 0) {
    const v = videoLoadQueue.shift();
    if (!v || !v.isConnected) continue;
    if (v.getAttribute('src') && v.getAttribute('preload') !== 'none') continue;
    activeVideoLoads++;
    let settled = false;
    const done = (aborted = false) => {
      if (settled) return;
      settled = true;
      if (v._loadTimer) {
        clearTimeout(v._loadTimer);
        v._loadTimer = null;
      }
      v._abortLoad = null;
      if (!aborted && v.isConnected && v.getAttribute('src')) {
        setVideoOverlayState(v, true);
      }
      activeVideoLoads = Math.max(0, activeVideoLoads - 1);
      pumpVideoLoadQueue();
    };
    v._abortLoad = () => done(true);
    v.addEventListener('loadeddata', () => done(false), { once: true });
    v.addEventListener('loadedmetadata', () => done(false), { once: true });
    v.addEventListener('error', () => done(false), { once: true });
    v._loadTimer = setTimeout(() => done(false), 3200);
    setVideoOverlayState(v, false, '⏳ Đang tải khung hình video...');
    const dataSrc = v.getAttribute('data-src');
    if (dataSrc && !v.getAttribute('src')) {
      v.setAttribute('src', dataSrc);
    }
    v.setAttribute('preload', 'metadata');
    v.preload = 'metadata';
    if (v.readyState === 0) {
      try { v.load(); } catch (e) { done(false); }
    } else {
      done(false);
    }
  }
}

let imageLoadQueue = [];
let activeImageLoads = 0;
const MAX_CONCURRENT_IMAGE_LOADS = 4;

function pumpImageLoadQueue() {
  while (activeImageLoads < MAX_CONCURRENT_IMAGE_LOADS && imageLoadQueue.length > 0) {
    const img = imageLoadQueue.shift();
    if (!img || !img.isConnected) continue;
    const ds = img.getAttribute('data-src');
    if (!ds) continue;
    activeImageLoads++;
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      if (img._imgTimer) {
        clearTimeout(img._imgTimer);
        img._imgTimer = null;
      }
      img._abortImgLoad = null;
      activeImageLoads = Math.max(0, activeImageLoads - 1);
      pumpImageLoadQueue();
    };
    img._abortImgLoad = done;
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
    img._imgTimer = setTimeout(done, 12000);
    img.src = ds;
    img.removeAttribute('data-src');
    if (img.complete && img.naturalWidth > 1) {
      onLazyImgLoaded(img);
      done();
    }
  }
}

function enqueueLazyImage(img) {
  if (!img || !img.isConnected) return;
  if (!img.getAttribute('data-src')) return;
  if (!imageLoadQueue.includes(img)) {
    imageLoadQueue.push(img);
    pumpImageLoadQueue();
  }
}

function cleanupVideosInContainer(root) {
  if (!root) return;
  if (root.id === 'todoList' || root.id === 'pageTodoDetail' || root.classList?.contains('todo-body')) {
    videoLoadQueue = [];
    activeVideoLoads = 0;
    imageLoadQueue = [];
    activeImageLoads = 0;
  }
  if (typeof audioStopTimer !== 'undefined' && audioStopTimer) {
    clearTimeout(audioStopTimer);
    audioStopTimer = null;
  }
  root.querySelectorAll('audio').forEach(a => {
    try { a.pause(); } catch (e) {}
  });
  root.querySelectorAll('img.lazy-img').forEach(img => {
    const iq = imageLoadQueue.indexOf(img);
    if (iq >= 0) imageLoadQueue.splice(iq, 1);
    if (img._abortImgLoad) {
      try { img._abortImgLoad(); } catch (e) {}
    }
    if (imageLazyObserver) {
      try { imageLazyObserver.unobserve(img); } catch (e) {}
    }
    const curSrc = img.getAttribute('src') || '';
    if (curSrc && curSrc !== TRANSPARENT_PIXEL && !img.complete) {
      try { img.src = TRANSPARENT_PIXEL; } catch (e) {}
    }
  });
  root.querySelectorAll('video').forEach(v => {
    const qIdx = videoLoadQueue.indexOf(v);
    if (qIdx >= 0) {
      videoLoadQueue.splice(qIdx, 1);
    }
    if (v._dwellTimer) {
      clearTimeout(v._dwellTimer);
      v._dwellTimer = null;
    }
    if (v._abortLoad) {
      try { v._abortLoad(); } catch (e) {}
    }
    if (videoObserver) {
      try { videoObserver.unobserve(v); } catch (e) {}
    }
    if (offscreenVideoObserver) {
      try { offscreenVideoObserver.unobserve(v); } catch (e) {}
    }
    if (v.getAttribute('src') || v.getAttribute('preload') !== 'none' || v.readyState > 0) {
      try {
        v.pause();
        v.removeAttribute('src');
        v.setAttribute('preload', 'none');
        v.load();
      } catch (e) {}
    }
  });
}

function upgradeInlineVideo(v) {
  if (!v || !v.isConnected) return;
  if ((!v.getAttribute('src') || v.getAttribute('preload') === 'none') && !videoLoadQueue.includes(v)) {
    setVideoOverlayState(v, false, '⏳ Đang xếp hàng tải video...');
    videoLoadQueue.push(v);
    pumpVideoLoadQueue();
  }
}

function unloadOffscreenIdleVideo(v) {
  if (!v || !v.isConnected || !v.paused) return;
  const qIdx = videoLoadQueue.indexOf(v);
  if (qIdx >= 0) {
    videoLoadQueue.splice(qIdx, 1);
    setVideoOverlayState(v, false, 'Đang chờ tải video (Lazy Load)...');
    if (videoObserver) videoObserver.observe(v);
    return;
  }
  if (!v.getAttribute('src')) return;
  try {
    if (v._abortLoad) {
      try { v._abortLoad(); } catch (e) {}
    }
    v.pause();
    v.removeAttribute('src');
    v.setAttribute('preload', 'none');
    v.preload = 'none';
    v.load();
    setVideoOverlayState(v, false, 'Đang chờ tải video (Lazy Load)...');
    if (videoObserver) videoObserver.observe(v);
  } catch (e) {}
}

function setupLazyVideoObserver(scopeEl) {
  if (!scopeEl) {
    if (videoObserver) {
      videoObserver.disconnect();
      videoObserver = null;
    }
    if (offscreenVideoObserver) {
      offscreenVideoObserver.disconnect();
      offscreenVideoObserver = null;
    }
  }
  const root = scopeEl || document;
  const videos = Array.from(root.querySelectorAll('video.shot-inline-video'));
  if (!('IntersectionObserver' in window)) {
    videos.slice(0, 4).forEach(upgradeInlineVideo);
    return;
  }
  if (!videoObserver) {
    videoObserver = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        const v = entry.target;
        if (entry.isIntersecting) {
          if (!v._dwellTimer) {
            setVideoOverlayState(v, false, '⏳ Đang chuẩn bị tải video...');
            v._dwellTimer = setTimeout(() => {
              v._dwellTimer = null;
              upgradeInlineVideo(v);
              try { obs.unobserve(v); } catch (e) {}
            }, 140);
          }
        } else if (v._dwellTimer) {
          clearTimeout(v._dwellTimer);
          v._dwellTimer = null;
          if (!v.getAttribute('src')) {
            setVideoOverlayState(v, false, 'Đang chờ tải video (Lazy Load)...');
          }
        }
      });
    }, { rootMargin: '140px 0px', threshold: 0.01 });
  }
  if (!offscreenVideoObserver) {
    offscreenVideoObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) {
          unloadOffscreenIdleVideo(entry.target);
        }
      });
    }, { rootMargin: '1500px 0px' });
  }
  videos.forEach(v => {
    if (!v.getAttribute('src') || v.getAttribute('preload') === 'none') {
      videoObserver.observe(v);
    }
    offscreenVideoObserver.observe(v);
  });
  setupLazyImageObserver(root);
  setupSectionLazyObserver(root);
}

function onLazyImgLoaded(imgEl) {
  if (!imgEl) return;
  const wrap = imgEl.closest('.img-lazy-wrap');
  if (wrap) {
    const loader = wrap.querySelector('.img-lazy-loader');
    if (loader) loader.classList.add('is-loaded');
  }
}

function setupLazyImageObserver(scopeEl) {
  const root = scopeEl || document;
  const imgs = Array.from(root.querySelectorAll('img.lazy-img[data-src]'));
  if (!imgs.length) return;
  if (!('IntersectionObserver' in window)) {
    imgs.forEach(enqueueLazyImage);
    return;
  }
  if (!imageLazyObserver) {
    imageLazyObserver = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const img = entry.target;
        enqueueLazyImage(img);
        try { obs.unobserve(img); } catch (e) {}
      });
    }, { rootMargin: '160px 0px' });
  }
  imgs.forEach(img => imageLazyObserver.observe(img));
}

function triggerSentinelLoadIfReady(el) {
  if (!el || !el.isConnected) return;
  if (el.getAttribute('data-auto') !== '1') return;
  if (window.scrollY < 20) return;
  if (el._lazyTimer) return;
  const kind = el.getAttribute('data-lazy-kind');
  const itemId = el.getAttribute('data-item-id');
  el._lazyTimer = setTimeout(() => {
    el._lazyTimer = null;
    if (!el.isConnected) return;
    if (kind === 'shots') {
      loadMoreShotsBatch(itemId, SHOT_LAZY_BATCH_SIZE, false);
    } else if (kind === 'uploaded_imgs') {
      loadMoreImagesBatch(itemId, 'uploaded', IMG_LAZY_BATCH_SIZE);
    } else if (kind === 'ref_imgs') {
      loadMoreImagesBatch(itemId, 'reference', IMG_LAZY_BATCH_SIZE);
    }
  }, 160);
}

let sentinelScrollBound = false;
function setupSectionLazyObserver(scopeEl) {
  if (!('IntersectionObserver' in window)) return;
  if (!sectionLazyObserver) {
    sectionLazyObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const el = entry.target;
        el._isIntersecting = entry.isIntersecting;
        if (!entry.isIntersecting) {
          if (el._lazyTimer) {
            clearTimeout(el._lazyTimer);
            el._lazyTimer = null;
          }
          return;
        }
        triggerSentinelLoadIfReady(el);
      });
    }, { rootMargin: '120px 0px' });
  }
  if (!sentinelScrollBound) {
    sentinelScrollBound = true;
    window.addEventListener('scroll', () => {
      if (window.scrollY < 20) return;
      document.querySelectorAll('.lazy-section-sentinel[data-auto="1"]').forEach(el => {
        if (el._isIntersecting) triggerSentinelLoadIfReady(el);
      });
    }, { passive: true });
  }
  const root = scopeEl || document;
  const sentinels = root.classList?.contains('lazy-section-sentinel')
    ? [root]
    : Array.from(root.querySelectorAll('.lazy-section-sentinel'));
  sentinels.forEach(s => sectionLazyObserver.observe(s));

  // Trigger checklist infinite scroll when scrolling inside .checklist-table-wrap
  const tableWraps = root.classList?.contains('checklist-table-wrap')
    ? [root]
    : Array.from(root.querySelectorAll('.checklist-table-wrap'));
  tableWraps.forEach(tw => {
    if (tw.dataset.infiniteBound === '1') return;
    tw.dataset.infiniteBound = '1';
    tw.addEventListener('scroll', () => {
      const itemId = tw.getAttribute('data-item-id');
      if (!itemId) return;
      if (tw.scrollTop + tw.clientHeight >= tw.scrollHeight - 90) {
        const rows = getFilteredChecklistRowsForItem(itemId);
        const curLimit = getChecklistRenderLimit(itemId, rows.length);
        if (curLimit < rows.length && !tw._loadingMore) {
          tw._loadingMore = true;
          setTimeout(() => {
            tw._loadingMore = false;
            if (!tw.isConnected) return;
            loadMoreChecklistRowsBatch(itemId, CHECKLIST_LAZY_BATCH_SIZE, false);
          }, 120);
        }
      }
    }, { passive: true });
  });
}

// --- Job Cards Infinite Scroll in #todoList ---
function cancelPendingTodoAppendTimer() {
  if (todoAppendTimer) {
    clearTimeout(todoAppendTimer);
    todoAppendTimer = null;
  }
  isAppendingTodoBatch = false;
}

function updateSummaryCounterText() {
  const el = document.getElementById('summaryText');
  if (!el) return;
  const filteredShorts = getFilteredShorts();
  if (currentStatusFilter === 'shorts') {
    el.textContent = `Hiển thị ${filteredShorts.length} / ${allShorts.length} sản phẩm Short video`;
    return;
  }
  const shortSuffix = filteredShorts.length ? ` · ${filteredShorts.length} Short video` : '';
  if (!currentFilteredTodos.length) {
    el.textContent = `Hiển thị 0 / ${allTodos.length} công việc${shortSuffix}`;
    return;
  }
  if (renderedTodoCount < currentFilteredTodos.length) {
    el.textContent = `Đang tải ${renderedTodoCount} / ${currentFilteredTodos.length} công việc (Cuộn xuống tự tải thêm · Tổng ${allTodos.length})${shortSuffix}`;
  } else {
    el.textContent = `Hiển thị ${currentFilteredTodos.length} / ${allTodos.length} công việc${shortSuffix}`;
  }
}

function renderTodoInfiniteSentinelHtml() {
  const rem = Math.max(0, currentFilteredTodos.length - renderedTodoCount);
  if (rem <= 0) {
    if (!currentFilteredTodos.length) return '';
    return `
      <div id="todoInfiniteDoneBanner" class="infinite-done-banner">
        ✅ Đã hiển thị đầy đủ ${currentFilteredTodos.length} / ${allTodos.length} công việc TODO — Bấm vào bất kỳ công việc nào để mở Trang Chi Tiết
      </div>`;
  }
  const nextCount = Math.min(TODO_INFINITE_BATCH_SIZE, rem);
  return `
    <div id="todoInfiniteSentinel" class="infinite-scroll-sentinel" data-testid="todo-infinite-sentinel">
      <div class="infinite-sentinel-status">
        <span class="spinner-ring"></span>
        <div>
          <div id="todoInfiniteStatusText">
            Đang tự động tải thêm công việc khi cuộn xuống (Đang hiển thị ${renderedTodoCount} / ${currentFilteredTodos.length} công việc)...
          </div>
          <div class="infinite-sentinel-sub">
            Chế độ Infinite Scroll & Lazy Load giúp tiết kiệm bộ nhớ RAM/CPU trình duyệt.
          </div>
        </div>
      </div>
      <div class="infinite-sentinel-actions">
        <button type="button" class="btn-primary btn-xs" onclick="loadMoreTodosNow()">
          ↓ Tải tiếp ${nextCount} công việc ngay (Còn ${rem} bài)
        </button>
        <button type="button" class="btn-xs" onclick="loadAllRemainingTodosNow()">
          ⚡ Tải hết ${currentFilteredTodos.length} bài
        </button>
      </div>
    </div>`;
}

function setupTodoInfiniteScrollObserver() {
  if (todoInfiniteObserver) {
    todoInfiniteObserver.disconnect();
    todoInfiniteObserver = null;
  }
  const sentinel = document.getElementById('todoInfiniteSentinel');
  if (!sentinel || !('IntersectionObserver' in window)) return;
  todoInfiniteObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) {
        if (todoAppendTimer) {
          cancelPendingTodoAppendTimer();
          const st = document.getElementById('todoInfiniteStatusText');
          if (st) {
            st.textContent = `Đang tự động tải thêm công việc khi cuộn xuống (Đang hiển thị ${renderedTodoCount} / ${currentFilteredTodos.length} công việc)...`;
          }
        }
        return;
      }
      if (entry.isIntersecting && !isAppendingTodoBatch && renderedTodoCount < currentFilteredTodos.length) {
        isAppendingTodoBatch = true;
        const st = document.getElementById('todoInfiniteStatusText');
        if (st) {
          st.textContent = `⏳ Đang tải tiếp công việc (${renderedTodoCount} / ${currentFilteredTodos.length})...`;
        }
        todoAppendTimer = setTimeout(() => {
          todoAppendTimer = null;
          isAppendingTodoBatch = false;
          appendNextTodoCardsBatch(TODO_INFINITE_BATCH_SIZE);
        }, 150);
      }
    });
  }, { rootMargin: '320px 0px' });
  todoInfiniteObserver.observe(sentinel);
}

function appendNextTodoCardsBatch(batchSize = TODO_INFINITE_BATCH_SIZE) {
  cancelPendingTodoAppendTimer();
  const container = document.getElementById('todoList');
  if (!container) return;
  const oldSentinel = document.getElementById('todoInfiniteSentinel');
  if (oldSentinel) oldSentinel.remove();
  const oldDone = document.getElementById('todoInfiniteDoneBanner');
  if (oldDone) oldDone.remove();

  const slice = currentFilteredTodos.slice(renderedTodoCount, renderedTodoCount + batchSize);
  if (!slice.length) {
    updateSummaryCounterText();
    return;
  }

  let gridEl = document.getElementById('todoSummaryGrid');
  if (!gridEl) {
    gridEl = document.createElement('div');
    gridEl.id = 'todoSummaryGrid';
    gridEl.className = 'todo-summary-grid';
    container.appendChild(gridEl);
  }

  const frag = document.createElement('div');
  frag.innerHTML = slice.map(t => renderTodoSummaryCard(t)).join('');
  const newCards = Array.from(frag.children);
  newCards.forEach(card => gridEl.appendChild(card));
  renderedTodoCount += slice.length;

  const footerHtml = renderTodoInfiniteSentinelHtml();
  if (footerHtml) {
    container.insertAdjacentHTML('beforeend', footerHtml);
  }
  updateSummaryCounterText();
  setupTodoInfiniteScrollObserver();
}

function loadMoreTodosNow() {
  cancelPendingTodoAppendTimer();
  appendNextTodoCardsBatch(TODO_INFINITE_BATCH_SIZE);
}

function loadAllRemainingTodosNow() {
  cancelPendingTodoAppendTimer();
  const rem = currentFilteredTodos.length - renderedTodoCount;
  if (rem > 0) {
    appendNextTodoCardsBatch(rem);
  }
}

function ensureTodoCardRendered(itemId) {
  if (!itemId) return;
  if (document.getElementById('card-' + itemId)) return;
  const idx = currentFilteredTodos.findIndex(t => t.item_id === itemId);
  if (idx >= renderedTodoCount) {
    cancelPendingTodoAppendTimer();
    appendNextTodoCardsBatch(idx - renderedTodoCount + 1);
  }
}

function getJobProgressRatio(t) {
  const d = detailsCache[t.item_id] || t;
  const cl = d.shot_checklist || {};
  const vp = d.video_progress || {};
  const exp = Number(cl.expected_total_duration_sec ?? d.audio_duration_sec ?? 0);
  const gen = Number(cl.generated_total_duration_sec ?? vp.uploaded_videos_duration_sec ?? 0);
  return exp > 0 ? (gen / exp) : 0;
}

function getFilteredShorts() {
  const searchEl = document.getElementById('searchBox');
  const kw = ((searchEl && searchEl.value) || '').trim().toLowerCase();
  const list = allShorts.filter(s => {
    if (currentStatusFilter === 'ready_to_assemble') return false;
    if (currentStatusFilter === 'assembled' && s.status !== 'ready') return false;
    if (currentStatusFilter === 'todo' && ['ready', 'failed', 'cancelled'].includes(s.status)) return false;
    if (!kw) return true;
    return (s.title || '').toLowerCase().includes(kw) || (s.id || '').toLowerCase().includes(kw);
  });
  return list.slice().sort((a, b) => {
    const ar = a.status === 'ready' ? 1 : 0;
    const br = b.status === 'ready' ? 1 : 0;
    if (ar !== br) return br - ar;
    return Number(b.created_at || 0) - Number(a.created_at || 0);
  });
}

function renderShortSummaryCard(rawShort) {
  const s = shortDetailsCache[rawShort.id] || rawShort;
  const sid = s.id;
  const ready = s.status === 'ready' && !!s.video_asset_id;
  const cardClass = ready ? 'is-assembled' : (s.status === 'failed' ? 'is-todo' : 'is-ready');
  const modeLabel = s.mode === 'upload' ? 'Upload MP4' : (SHORT_MODE_NAMES[s.mode] || s.mode || 'Short');
  return `
  <article class="todo-summary-card ${cardClass}" id="short-card-${esc(sid)}" data-short-id="${esc(sid)}" role="button" tabindex="0" data-kb-click="1" aria-label="Mở Short Video: ${esc(s.title)}" onclick="openShortDetailPage('${esc(sid)}')">
    <div>
      <div class="summary-card-head">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0">
          <span class="pill pill-accent">📱 SHORT 9:16</span>
          <span class="pill pill-ghost mono" title="Mã Short">${esc(sid)}</span>
          <span class="pill pill-ghost mono">${esc((s.lang || 'vi').toUpperCase())}</span>
        </div>
        <span class="pill ${SHORT_STATUS_PILL[s.status] || 'pill-ghost'}">${esc(s.status_label || s.status)}</span>
      </div>
      <h2 class="todo-summary-title" title="${esc(s.title)}">${esc(s.title)}</h2>
    </div>

    <div class="summary-badges-row">
      <span class="pill pill-ghost">🎬 ${esc(modeLabel)}</span>
      <span class="pill ${s.video_asset_id ? 'pill-ok' : 'pill-ghost'}">${s.video_asset_id ? '✅ Đã có MP4 9:16' : '⏳ Chưa có MP4'}</span>
      ${Number(s.duration_s || 0) > 0 ? `<span class="pill pill-ghost mono">⏱ ${Number(s.duration_s).toFixed(1)}s</span>` : ''}
      ${Number(s.word_count || 0) > 0 ? `<span class="pill pill-ghost mono">📝 ${Number(s.word_count)} từ</span>` : ''}
      ${s.srt_asset_id ? `<span class="pill pill-ghost mono">.SRT</span>` : ''}
    </div>

    ${renderTodoYoutubeStatusBanner(s, 'short')}

    <div class="summary-card-foot" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:space-between">
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        ${s.video_asset_id ? `<button type="button" class="btn-sm" onclick="event.stopPropagation();openShortPreviewModal('${esc(sid)}')">▶ Xem MP4</button>` : ''}
        ${renderYoutubeActionButton(s, 'short', 'btn-primary btn-sm')}
      </div>
      <button type="button" class="btn-sm" onclick="event.stopPropagation();openShortDetailPage('${esc(sid)}')">
        Chi tiết →
      </button>
    </div>
  </article>`;
}

function rerenderSingleShortCard(shortId) {
  const s = shortDetailsCache[shortId] || allShorts.find(x => x.id === shortId);
  const existing = document.getElementById('short-card-' + shortId);
  if (!s || !existing) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = renderShortSummaryCard(s).trim();
  if (tmp.firstElementChild) existing.replaceWith(tmp.firstElementChild);
}

function openShortPreviewModal(shortId) {
  const s = shortDetailsCache[shortId] || allShorts.find(x => x.id === shortId);
  if (!s || !s.video_asset_id) {
    showToast('Short này chưa có video MP4.', 'warn', 2800);
    return;
  }
  openWebModal({
    title: `📱 Short Video 9:16 — ${s.title}`,
    subtitle: `${s.id} · ${Number(s.duration_s || 0).toFixed(1)}s · ${(s.lang || 'vi').toUpperCase()}`,
    bodyHtml: `
      <div style="display:flex;justify-content:center;background:#0B1120;border-radius:12px;padding:12px">
        <video controls autoplay playsinline src="/api/v1/shorts/${esc(shortId)}/video" style="max-height:72vh;width:auto;max-width:100%;aspect-ratio:9/16;border-radius:10px;background:#000"></video>
      </div>
    `,
    footHtml: `
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        ${renderYoutubeActionButton(s, 'short', 'btn-primary btn-sm')}
        <button type="button" class="btn-sm" onclick="closeWebModal();openShortDetailPage('${esc(shortId)}')">Mở trang chi tiết Short →</button>
      </div>
      <span style="font-size:12px;color:var(--fg-muted)">9:16 Vertical Short</span>
    `
  });
}

function renderFilteredTodos() {
  const searchEl = document.getElementById('searchBox');
  const kw = ((searchEl && searchEl.value) || '').trim().toLowerCase();
  const filtered = currentStatusFilter === 'shorts'
    ? []
    : allTodos.filter(t => {
        const eff = getEffectiveStatus(t);
        if (currentStatusFilter && eff !== currentStatusFilter) return false;
        if (!kw) return true;
        return (t.title || '').toLowerCase().includes(kw) || (t.item_id || '').toLowerCase().includes(kw);
      });
  const filteredShorts = getFilteredShorts();

  if (currentSortOrder === 'progress_desc') {
    filtered.sort((a, b) => getJobProgressRatio(b) - getJobProgressRatio(a));
  } else if (currentSortOrder === 'progress_asc') {
    filtered.sort((a, b) => getJobProgressRatio(a) - getJobProgressRatio(b));
  } else if (currentSortOrder === 'duration_desc') {
    filtered.sort((a, b) => Number(b.audio_duration_sec || 0) - Number(a.audio_duration_sec || 0));
  }

  cancelPendingTodoAppendTimer();
  currentFilteredTodos = filtered;
  renderedTodoCount = 0;

  const container = document.getElementById('todoList');
  if (!container) return;
  cleanupVideosInContainer(container);
  if (todoInfiniteObserver) {
    todoInfiniteObserver.disconnect();
    todoInfiniteObserver = null;
  }
  if (!filtered.length && !filteredShorts.length) {
    updateSummaryCounterText();
    const emptyMsg = currentStatusFilter === 'shorts'
      ? `Chưa có sản phẩm Short video nào${kw ? ` khớp "${kw}"` : ''}. Bạn có thể tạo hoặc upload MP4 mới trong Short Studio.`
      : `Không có công việc hoặc sản phẩm nào khớp bộ lọc đang chọn (Hệ thống có ${allTodos.length} bài dài và ${allShorts.length} Short).`;
    container.innerHTML = `
      <div class="panel" style="text-align:center;padding:40px 20px;color:var(--fg-muted)">
        <div style="font-size:14px;font-weight:600;color:var(--fg);margin-bottom:6px">
          ${esc(emptyMsg)}
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <button type="button" class="btn-primary btn-sm" onclick="document.getElementById('searchBox').value='';setStatusFilter('')">
            Xem tất cả (${allTodos.length + allShorts.length})
          </button>
          ${currentStatusFilter === 'shorts' ? `<button type="button" class="btn-sm" onclick="goShortStudioPage()">+ Tạo / Upload Short Video</button>` : ''}
        </div>
      </div>`;
    return;
  }

  container.innerHTML = '';
  if (filteredShorts.length) {
    container.insertAdjacentHTML('beforeend', `
      <section id="shortSummarySection" style="margin-bottom:18px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:2px 0 10px">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <h3 style="margin:0;font-size:15px;font-weight:700">📱 Sản phẩm Short Video (${filteredShorts.length})</h3>
            <span class="pill pill-ghost" style="font-size:11px">Đăng ngay hoặc hẹn giờ YouTube trực tiếp từ đây</span>
          </div>
          <button type="button" class="btn-xs" onclick="goShortStudioPage()">+ Short Studio</button>
        </div>
        <div id="shortSummaryGrid" class="todo-summary-grid">
          ${filteredShorts.map(s => renderShortSummaryCard(s)).join('')}
        </div>
      </section>
    `);
  }

  if (filtered.length) {
    if (filteredShorts.length) {
      container.insertAdjacentHTML('beforeend', `
        <div style="display:flex;align-items:center;gap:8px;margin:6px 0 10px">
          <h3 style="margin:0;font-size:15px;font-weight:700">🎬 Sản phẩm Video Dài 16:9 (${filtered.length})</h3>
        </div>
      `);
    }
    appendNextTodoCardsBatch(TODO_INFINITE_BATCH_SIZE);
  } else {
    updateSummaryCounterText();
  }
}

function rerenderSingleCard(itemId) {
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId);
  if (!item) {
    renderFilteredTodos();
    return;
  }
  const existingSummary = document.getElementById('card-' + itemId);
  if (existingSummary) {
    const tmp = document.createElement('div');
    tmp.innerHTML = renderTodoSummaryCard(item).trim();
    const newSummary = tmp.firstElementChild;
    if (newSummary) {
      existingSummary.replaceWith(newSummary);
    }
  }
  if (activeDetailItemId === itemId) {
    renderTodoDetailPage(itemId);
  }
}

// --- PAGE 1: Compact Summary Card (Zero Heavy Grids, Zero Inline Videos) ---
function renderTodoSummaryCard(summaryItem) {
  const item = detailsCache[summaryItem.item_id] || summaryItem;
  const itemId = item.item_id;
  const jobIdx = allTodos.findIndex(x => x.item_id === itemId);
  const numStr = String(jobIdx >= 0 ? jobIdx + 1 : 1).padStart(2, '0');
  const vp = item.video_progress || {};
  const cl = item.shot_checklist || {
    total_shots: item.total_shots || 0,
    checked_shots_count: vp.uploaded_videos_count || 0,
    unchecked_shots_count: Math.max(0, (item.total_shots || 0) - (vp.uploaded_videos_count || 0)),
    expected_total_duration_sec: item.audio_duration_sec || 0,
    generated_total_duration_sec: vp.uploaded_videos_duration_sec || 0,
    remaining_duration_to_generate_sec: vp.shortfall_sec || 0,
    delta_total_duration_sec: Number((vp.uploaded_videos_duration_sec || 0) - (item.audio_duration_sec || 0)),
    total_delta_duration_sec: Number((vp.uploaded_videos_duration_sec || 0) - (item.audio_duration_sec || 0)),
    shots: item.shots || []
  };

  const totalShots = Number(cl.total_shots || 0);
  const checkedShots = Number(cl.checked_shots_count || 0);
  const missingShots = Math.max(0, totalShots - checkedShots);
  const shotsArr = Array.isArray(cl.shots) && cl.shots.length ? cl.shots : (item.shots || []);
  const withVideoCount = shotsArr.filter(s => s.has_video).length || Number(vp.uploaded_videos_count || 0);
  const expectedSec = Number(cl.expected_total_duration_sec || item.audio_duration_sec || 0);
  const generatedSec = Number(cl.generated_total_duration_sec || vp.uploaded_videos_duration_sec || 0);
  const remainingSec = Number(cl.remaining_duration_to_generate_sec ?? vp.shortfall_sec ?? 0);
  const totalDelta = Number(cl.total_delta_duration_sec ?? cl.delta_total_duration_sec ?? (generatedSec - expectedSec));
  const deltaSign = totalDelta > 0 ? '+' : '';
  const pct = expectedSec > 0 ? Math.min(100, Math.round((generatedSec / expectedSec) * 100)) : 0;

  const eff = getEffectiveStatus(item);
  const statusBadgeHtml = eff === 'assembled'
    ? `<span class="pill pill-assembled">✅ Đã ráp thành phẩm</span>`
    : (eff === 'ready_to_assemble'
      ? `<span class="pill pill-ready">⚡ Sẵn sàng ráp</span>`
      : `<span class="pill pill-todo">⏳ Đang chờ tạo Shot</span>`);
  const cardBorderClass = eff === 'assembled' ? 'is-assembled' : (eff === 'ready_to_assemble' ? 'is-ready' : 'is-todo');

  const uploadedImgsCount = (item.uploaded_images || []).length;
  const refImgsCount = (item.reference_images || []).length;
  const totalImgsCount = uploadedImgsCount + refImgsCount;

  return `
  <article class="todo-summary-card ${cardBorderClass}" id="card-${esc(itemId)}" data-item-id="${esc(itemId)}" role="button" tabindex="0" data-kb-click="1" aria-label="Mở chi tiết công việc: ${esc(item.title_vi || item.title)}" onclick="openTodoDetailPage('${esc(itemId)}')">
    <div>
      <div class="summary-card-head">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0">
          <span class="job-num-badge mono">#${numStr}</span>
          <span class="pill pill-ghost mono" title="Mã công việc">${esc(itemId)}</span>
        </div>
        ${statusBadgeHtml}
      </div>
      <h2 class="todo-summary-title" title="${esc(item.title_vi || item.title)}">${esc(item.title_vi || item.title)}</h2>
    </div>

    <div class="summary-progress-wrap">
      <div class="summary-progress-head">
        <span>Tiến độ Shot: <b class="mono">${checkedShots}/${totalShots} shot</b> <span class="mono" style="color:var(--accent);font-weight:700">(${pct}%)</span></span>
        <span class="mono" style="font-size:11px;font-weight:600;color:${missingShots > 0 ? 'var(--held)' : 'var(--ok)'}">
          ${missingShots > 0 ? `Còn thiếu ${missingShots} shot` : 'Đủ 100% shot'}
        </span>
      </div>
      <div class="progress-track" style="height:7px;margin:0">
        <div class="progress-fill ${vp.is_duration_sufficient ? 'is-ok' : (pct > 50 ? '' : 'is-warn')}" style="width:${pct}%"></div>
      </div>
    </div>

    <div class="summary-kpi-grid">
      <div class="summary-kpi-cell">
        <div class="sk-lbl">Dự kiến</div>
        <div class="sk-val">${fmtSec(expectedSec)}</div>
      </div>
      <div class="summary-kpi-cell">
        <div class="sk-lbl">Thực tế</div>
        <div class="sk-val" style="color:var(--accent)">${fmtSec(generatedSec)}</div>
      </div>
      <div class="summary-kpi-cell">
        <div class="sk-lbl">Độ lệch (Δ)</div>
        <div class="sk-val ${Math.abs(totalDelta) <= 0.5 ? 'delta-ok' : (totalDelta < 0 ? 'delta-short' : 'delta-over')}">${deltaSign}${totalDelta.toFixed(1)}s</div>
      </div>
      <div class="summary-kpi-cell">
        <div class="sk-lbl">Cần gen thêm</div>
        <div class="sk-val ${remainingSec <= 0.5 ? 'delta-ok' : 'delta-short'}">+${fmtSec(remainingSec)}</div>
      </div>
    </div>

    <div class="summary-badges-row">
      <span class="pill ${item.audio_url ? 'pill-ok' : 'pill-ghost'}">🎵 ${item.audio_url ? 'Đã có Audio' : 'Chưa có Audio'}</span>
      ${item.final_video_url
        ? `<span class="pill pill-ok">🎬 Đã có Video thành phẩm</span>`
        : `<span class="pill pill-ghost">🎬 Chưa ráp thành phẩm</span>`}
      <span class="pill ${totalImgsCount > 0 ? 'pill-accent' : 'pill-ghost'}">🖼️ ${totalImgsCount} Ảnh tư liệu</span>
      <span class="pill ${checkedShots > 0 ? 'pill-ok' : 'pill-ghost'}">📋 ${checkedShots}/${totalShots} Checklist đã tick</span>
      ${rbTodoChipHtml(itemId)}
    </div>

    ${item.youtube_upload ? renderTodoYoutubeStatusBanner(item, 'todo') : ''}

    <div class="summary-card-foot">
      <div class="summary-flow-chips" aria-label="Mở nhanh từng tab công việc">
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'overview')" title="Tab 1: Tổng quan & Kịch bản">
          📊 1. Tổng quan
        </button>
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'final_video')" title="Tab 2: Video Thành Phẩm">
          🏆 2. Thành phẩm
        </button>
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'shots')" title="Tab 3: Lưới 4 Video / Dòng">
          🎬 3. Shot (${withVideoCount}/${totalShots})
        </button>
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'checklist')" title="Tab 4: Bảng Checklist Agent">
          📋 4. Checklist (${checkedShots}/${totalShots})
        </button>
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'images')" title="Tab 5: Ảnh Keyframe & Tư Liệu">
          🖼️ 5. Ảnh (${totalImgsCount})
        </button>
        <button type="button" class="btn-xs flow-chip" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'upload_audio')" title="Tab 6: Tải lên & Tinh chỉnh Audio">
          📤 6. Tải lên & Audio
        </button>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${eff === 'assembled' && item.final_video_url ? renderYoutubeActionButton(item, 'todo', 'btn-ok btn-sm') : ''}
        <button type="button" class="btn-primary summary-cta-btn" style="flex:1" onclick="event.stopPropagation();openTodoDetailPage('${esc(itemId)}', 'overview')">
          👉 Mở trang chi tiết công việc (6 Tab Lazy Load) →
        </button>
      </div>
    </div>
  </article>`;
}

// --- PAGE 2: Ultra-Compact Job Detail Page with Sticky Navigation & 6 Strictly Lazy-Loaded Tabs ---
function renderTodoDetailPage(itemId) {
  const detailContainer = document.getElementById('pageTodoDetail');
  if (!detailContainer) return;
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId);
  if (!item) {
    detailContainer.innerHTML = `
      <div class="panel" style="text-align:center;padding:40px 20px">
        <div style="font-size:15px;font-weight:700;margin-bottom:8px">Không tìm thấy công việc ${esc(itemId)}</div>
        <button type="button" class="btn-primary" onclick="backToTodoListPage()">← Quay lại Danh sách TODO</button>
      </div>`;
    return;
  }

  cleanupVideosInContainer(detailContainer);

  const jobIdx = allTodos.findIndex(x => x.item_id === itemId);
  const numStr = String(jobIdx >= 0 ? jobIdx + 1 : 1).padStart(2, '0');
  const prevItem = jobIdx > 0 ? allTodos[jobIdx - 1] : null;
  const nextItem = (jobIdx >= 0 && jobIdx < allTodos.length - 1) ? allTodos[jobIdx + 1] : null;

  const jobSelectOptions = allTodos.map((t, i) => {
    const n = String(i + 1).padStart(2, '0');
    const selected = t.item_id === itemId ? 'selected' : '';
    return `<option value="${esc(t.item_id)}" ${selected}>#${n} · ${esc(t.title)}</option>`;
  }).join('');

  detailContainer.innerHTML = `
    <nav class="detail-page-nav" aria-label="Điều hướng trang chi tiết công việc">
      <div class="detail-nav-left">
        <button type="button" class="btn-primary btn-sm" id="backToTodoListBtn" onclick="backToTodoListPage()">
          ← Danh sách TODO (${allTodos.length})
        </button>
        <div class="detail-breadcrumb">
          <a href="#/todos" class="crumb-link" onclick="event.preventDefault();backToTodoListPage()">Danh sách</a>
          <span class="crumb-link">/</span>
          <span class="mono" style="color:var(--accent);font-weight:700">#${numStr}</span>
          <span>·</span>
          <span class="crumb-title" title="${esc(item.title)}">${esc(item.title)}</span>
        </div>
      </div>
      <div class="detail-job-switcher">
        <button type="button" class="btn-sm" ${!prevItem ? 'disabled' : ''} onclick="${prevItem ? `openTodoDetailPage('${esc(prevItem.item_id)}')` : ''}">
          ◀ Bài trước${prevItem ? ` (#${String(jobIdx).padStart(2, '0')})` : ''}
        </button>
        <select class="detail-job-select" aria-label="Chuyển nhanh sang công việc khác" onchange="if(this.value)openTodoDetailPage(this.value)">
          ${jobSelectOptions}
        </select>
        <button type="button" class="btn-sm" ${!nextItem ? 'disabled' : ''} onclick="${nextItem ? `openTodoDetailPage('${esc(nextItem.item_id)}')` : ''}">
          Bài tiếp${nextItem ? ` (#${String(jobIdx + 2).padStart(2, '0')})` : ''} ▶
        </button>
      </div>
    </nav>
    ${renderTodoCard(item)}
  `;

  setupLazyVideoObserver(detailContainer);
}

function renderCompactAudioStrip(item) {
  const itemId = item.item_id;
  return `
  <section class="panel" id="audio-panel-${esc(itemId)}" style="padding:8px 12px;margin-bottom:10px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0">
        <span style="font-weight:600;font-size:12px">🔊 Audio EN Chuẩn (${fmtSec(item.audio_duration_sec)})</span>
        <span id="audioSegmentHint-${esc(itemId)}" class="mono" style="font-size:11px;color:var(--accent)"></span>
      </div>
      <audio id="audioPlayer-${esc(itemId)}" controls preload="none" src="${esc(item.audio_url)}" style="height:32px;max-width:100%;width:400px"></audio>
    </div>
  </section>`;
}

function buildChecklistFallback(item) {
  const vp = item.video_progress || {};
  return item.shot_checklist || {
    total_shots: item.total_shots || 0,
    checked_shots_count: vp.uploaded_videos_count || 0,
    unchecked_shots_count: Math.max(0, (item.total_shots || 0) - (vp.uploaded_videos_count || 0)),
    expected_total_duration_sec: item.audio_duration_sec || 0,
    generated_total_duration_sec: vp.uploaded_videos_duration_sec || 0,
    remaining_duration_to_generate_sec: vp.shortfall_sec || 0,
    delta_total_duration_sec: Number((vp.uploaded_videos_duration_sec || 0) - (item.audio_duration_sec || 0)),
    total_delta_duration_sec: Number((vp.uploaded_videos_duration_sec || 0) - (item.audio_duration_sec || 0)),
    shots: item.shots || []
  };
}

function renderDetailTabPaneHtml(summaryItem, requestedTab = '') {
  const item = detailsCache[summaryItem.item_id] || summaryItem;
  const itemId = item.item_id;
  const vp = item.video_progress || {};
  const cl = buildChecklistFallback(item);
  const activeTab = normalizeDetailFlowTab(requestedTab || detailFlowTabByItem[itemId] || 'overview');

  const shotsArr = Array.isArray(cl.shots) && cl.shots.length ? cl.shots : (item.shots || []);
  const withVideoCount = shotsArr.filter(s => s.has_video).length || Number(vp.uploaded_videos_count || 0);
  const uploadedImgsCount = (item.uploaded_images || []).length;
  const refImgsCount = (item.reference_images || []).length;
  const totalImgsCount = uploadedImgsCount + refImgsCount;

  const pct = cl.expected_total_duration_sec > 0
    ? Math.min(100, Math.round((cl.generated_total_duration_sec / cl.expected_total_duration_sec) * 100))
    : 0;
  const totalDelta = Number(cl.total_delta_duration_sec ?? cl.delta_total_duration_sec ?? 0);
  const deltaSign = totalDelta > 0 ? '+' : '';

  if (activeTab === 'overview') {
    const kpiStripHtml = `
      <div class="job-kpi-grid" style="margin-bottom:10px">
        <div class="kpi-box">
          <div class="k-lbl">1. Tiến Độ Shot Đã Gen</div>
          <div class="k-val">${cl.checked_shots_count} / ${cl.total_shots} shot</div>
          <div class="k-note">Còn trống ${cl.unchecked_shots_count} shot</div>
        </div>
        <div class="kpi-box">
          <div class="k-lbl">2. Thời Lượng Dự Kiến</div>
          <div class="k-val">${fmtSec(cl.expected_total_duration_sec)}</div>
          <div class="k-note">Khớp file Audio EN chuẩn</div>
        </div>
        <div class="kpi-box">
          <div class="k-lbl">3. Thời Lượng Đã Gen</div>
          <div class="k-val" style="color:var(--accent)">${fmtSec(cl.generated_total_duration_sec)}</div>
          <div class="k-note">Đạt ${pct}% thời lượng bài</div>
        </div>
        <div class="kpi-box">
          <div class="k-lbl">4. Độ Lệch Tổng Cộng</div>
          <div class="k-val ${Math.abs(totalDelta) <= 0.5 ? 'delta-ok' : (totalDelta < 0 ? 'delta-short' : 'delta-over')}">${deltaSign}${totalDelta.toFixed(2)}s</div>
          <div class="k-note">Thực tế trừ dự kiến</div>
        </div>
        <div class="kpi-box">
          <div class="k-lbl">5. Số Giây Cần Gen Thêm</div>
          <div class="k-val ${cl.remaining_duration_to_generate_sec <= 0.5 ? 'delta-ok' : 'delta-short'}">+${fmtSec(cl.remaining_duration_to_generate_sec)}</div>
          <div class="k-note">${cl.remaining_duration_to_generate_sec <= 0.5 ? 'Đã đủ thời lượng' : 'Agent cần gen bù'}</div>
        </div>
      </div>
      <div class="progress-track" style="margin-bottom:12px">
        <div class="progress-fill ${vp.is_duration_sufficient ? 'is-ok' : (pct > 50 ? '' : 'is-warn')}" style="width:${pct}%"></div>
      </div>`;

    const overviewFlowCardsHtml = `
      <div class="detail-overview-flow-grid">
        <div class="detail-flow-jump-card" role="button" tabindex="0" data-kb-click="1" onclick="switchDetailFlowTab('final_video', '${esc(itemId)}')">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px">
              <span style="font-weight:700;font-size:13px">🏆 Tab 2: Video Thành Phẩm</span>
              <span class="pill ${item.final_video_url ? 'pill-ok' : (vp.is_duration_sufficient ? 'pill-ready' : 'pill-warn')}">
                ${item.final_video_url ? 'Đã ráp MP4' : (vp.is_duration_sufficient ? 'Sẵn sàng ráp' : 'Chờ đủ shot')}
              </span>
            </div>
            <div style="font-size:11.5px;color:var(--fg-muted)">
              ${item.final_video_url
                ? 'Xem trực tiếp video thành phẩm (final_en.mp4), phóng to toàn màn hình hoặc ráp lại.'
                : `Kiểm tra trạng thái sẵn sàng ráp (${withVideoCount}/${cl.total_shots} shot) và xuất bản thành phẩm.`}
            </div>
          </div>
          <button type="button" class="${item.final_video_url ? 'btn-ok' : 'btn-sm'} btn-xs" style="width:100%">
            ${item.final_video_url ? '▶ Mở Xem Video Thành Phẩm →' : '🏆 Mở Trạng Thái Ráp Video →'}
          </button>
        </div>

        <div class="detail-flow-jump-card" role="button" tabindex="0" data-kb-click="1" onclick="switchDetailFlowTab('shots', '${esc(itemId)}')">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px">
              <span style="font-weight:700;font-size:13px">🎬 Tab 3: Lưới Shot Video</span>
              <span class="pill ${withVideoCount > 0 ? 'pill-accent' : 'pill-ghost'}">${withVideoCount}/${cl.total_shots} video</span>
            </div>
            <div style="font-size:11.5px;color:var(--fg-muted)">
              Lưới 4 cột chuẩn (Lazy Load 8 shot/lần), lọc ô trống thiếu video và tải nhanh vào từng Shot.
            </div>
          </div>
          <button type="button" class="btn-primary btn-xs" style="width:100%">
            🎬 Mở Lưới Shot (${cl.total_shots} Shot) →
          </button>
        </div>

        <div class="detail-flow-jump-card" role="button" tabindex="0" data-kb-click="1" onclick="switchDetailFlowTab('checklist', '${esc(itemId)}')">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px">
              <span style="font-weight:700;font-size:13px">📋 Tab 4: Bảng Checklist</span>
              <span class="pill ${cl.checked_shots_count > 0 ? 'pill-ok' : 'pill-ghost'}">${cl.checked_shots_count}/${cl.total_shots} đã tick</span>
            </div>
            <div style="font-size:11.5px;color:var(--fg-muted)">
              Tick xác nhận từng Shot, chỉnh thời lượng thực tế và đối chiếu độ lệch Δ (Lazy Load 12 dòng/lần).
            </div>
          </div>
          <button type="button" class="btn-xs" style="width:100%">
            📋 Mở Bảng Checklist Agent →
          </button>
        </div>

        <div class="detail-flow-jump-card" role="button" tabindex="0" data-kb-click="1" onclick="switchDetailFlowTab('images', '${esc(itemId)}')">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px">
              <span style="font-weight:700;font-size:13px">🖼️ Tab 5: Ảnh Keyframe & Tư Liệu</span>
              <span class="pill pill-accent">${uploadedImgsCount} KF · ${refImgsCount} Ref</span>
            </div>
            <div style="font-size:11.5px;color:var(--fg-muted)">
              Xem thư viện Keyframe & Visual Anchors (Lazy Load 12 ảnh/lần qua IntersectionObserver + Lightbox).
            </div>
          </div>
          <button type="button" class="btn-xs" style="width:100%">
            🖼️ Mở Thư Viện Ảnh (${totalImgsCount}) →
          </button>
        </div>

        <div class="detail-flow-jump-card" role="button" tabindex="0" data-kb-click="1" onclick="switchDetailFlowTab('upload_audio', '${esc(itemId)}')">
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:4px">
              <span style="font-weight:700;font-size:13px">📤 Tab 6: Tải Lên & Tinh Chỉnh Audio</span>
              <span class="pill pill-ghost">Upload & BGM</span>
            </div>
            <div style="font-size:11.5px;color:var(--fg-muted)">
              Tải video vào Shot, tải ảnh Keyframe/Character Sheet và tạo lại Audio EN theo nhịp mới kèm nhạc nền.
            </div>
          </div>
          <button type="button" class="btn-xs" style="width:100%">
            📤 Mở Tải Lên & Audio →
          </button>
        </div>
      </div>`;

    return `
      <div class="lazy-tab-pane" data-active-pane="overview">
        ${kpiStripHtml}
        ${renderAudioAndWebViewersBar(item)}
        ${overviewFlowCardsHtml}
      </div>`;
  }

  if (activeTab === 'final_video') {
    return `
      <div class="lazy-tab-pane" data-active-pane="final_video">
        ${renderFinalVideoTabSection(item, cl, vp)}
      </div>`;
  }

  if (activeTab === 'shots') {
    return `
      <div class="lazy-tab-pane" data-active-pane="shots">
        ${renderCompactAudioStrip(item)}
        ${renderFourColumnShotGridSection(item, cl)}
      </div>`;
  }

  if (activeTab === 'review_board') {
    return `
      <div class="lazy-tab-pane" data-active-pane="review_board">
        ${renderReviewBoardSection(item, cl)}
      </div>`;
  }

  if (activeTab === 'checklist') {
    return `
      <div class="lazy-tab-pane" data-active-pane="checklist">
        ${renderCompactAudioStrip(item)}
        ${renderAgentShotChecklistSection(item, cl)}
      </div>`;
  }

  if (activeTab === 'images') {
    const visualHtml = renderVisualAssetsSection(item, true) || `
      <section class="panel" style="text-align:center;padding:24px;color:var(--fg-muted);margin-bottom:0">
        <div style="font-size:14px;font-weight:600;color:var(--fg);margin-bottom:6px">Chưa có ảnh Keyframe hoặc ảnh tư liệu nào cho bài này</div>
        <div style="font-size:12px;margin-bottom:12px">Bạn có thể tải ảnh Keyframe / Character Sheet lên ở Tab 6.</div>
        <button type="button" class="btn-primary btn-sm" onclick="openUploadTabForImage('${esc(itemId)}')">
          📤 Chuyển sang Tab 6: Tải lên Ảnh / Video →
        </button>
      </section>`;
    return `
      <div class="lazy-tab-pane" data-active-pane="images">
        ${visualHtml}
      </div>`;
  }

  // activeTab === 'upload_audio'
  return `
    <div class="lazy-tab-pane" data-active-pane="upload_audio">
      ${renderUploadControlsSection(item)}
      ${renderFeedbackAndRegenSection(item, true)}
    </div>`;
}

function renderTodoCard(summaryItem) {
  const item = detailsCache[summaryItem.item_id] || summaryItem;
  const itemId = item.item_id;
  const jobIdx = allTodos.findIndex(x => x.item_id === itemId);
  const numStr = String(jobIdx >= 0 ? jobIdx + 1 : 1).padStart(2, '0');
  const vp = item.video_progress || {};
  const cl = buildChecklistFallback(item);

  const shotsArr = Array.isArray(cl.shots) && cl.shots.length ? cl.shots : (item.shots || []);
  const withVideoCount = shotsArr.filter(s => s.has_video).length || Number(vp.uploaded_videos_count || 0);
  const uploadedImgsCount = (item.uploaded_images || []).length;
  const refImgsCount = (item.reference_images || []).length;
  const totalImgsCount = uploadedImgsCount + refImgsCount;

  const effectiveStatus = (item.status === 'todo' && vp.ready_to_assemble) ? 'ready_to_assemble' : item.status;
  const statusLabel = STATUS_LABELS[effectiveStatus] || item.status;
  const activeTab = normalizeDetailFlowTab(detailFlowTabByItem[itemId] || 'overview');
  detailFlowTabByItem[itemId] = activeTab;

  return `
  <article class="todo-card detail-todo-card" id="detail-card-${esc(itemId)}">
    <div class="todo-summary">
      <div class="todo-title-wrap">
        <div class="todo-title-row">
          <span class="job-num-badge mono">#${numStr}</span>
          <span class="pill pill-ghost mono">${esc(itemId)}</span>
          <span class="pill ${statusPillClass(item.status)}">${esc(statusLabel)}</span>
          <h2 class="todo-title">${esc(item.title)}</h2>
        </div>
        <div class="todo-meta">
          <span>Kịch bản: <b>${cl.total_shots} shot</b></span>
          <span>Audio: <b class="mono">${fmtSec(item.audio_duration_sec)}</b></span>
          <span>Đã tick: <b class="mono">${cl.checked_shots_count}/${cl.total_shots}</b></span>
          <span>Cần gen thêm: <b class="mono" style="color:${cl.remaining_duration_to_generate_sec <= 0.5 ? 'var(--ok)' : 'var(--held)'}">+${fmtSec(cl.remaining_duration_to_generate_sec)}</b></span>
          ${rbTodoChipHtml(itemId)}
        </div>
      </div>
      <div class="todo-actions">
        ${(vp.has_thumbnail || item.has_thumbnail)
          ? `<span class="pill pill-ok" title="Đã có ảnh thumbnail cho video">🖼️ Có Thumbnail</span>`
          : `<span class="pill pill-warn" title="Bắt buộc có 1 ảnh thumbnail trước khi hoàn thành job">⚠️ Thiếu Thumbnail</span>`}
        <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 9px;border-radius:8px;border:1px solid var(--border);background:rgba(255,255,255,.04);cursor:pointer;user-select:none" title="Gắn chữ ký mờ @StickSto chuyển động chống re-up vào video thành phẩm">
          <input type="checkbox" id="wmToggle-${esc(itemId)}" checked onchange="syncWatermarkToggle('${esc(itemId)}', this.checked)">
          <span>🛡️ Chữ ký mờ @StickSto</span>
        </label>
        <button type="button" class="btn-sm" onclick="refreshSingleItem('${esc(itemId)}', true, this)">🔄 Đồng bộ Shot</button>
        <button type="button" class="btn-ok btn-sm" onclick="confirmAssemble('${esc(itemId)}', false, this)" ${(!vp.is_duration_sufficient || !(vp.has_thumbnail || item.has_thumbnail)) ? `disabled title="${!(vp.has_thumbnail || item.has_thumbnail) ? 'Bắt buộc có 1 ảnh thumbnail trước khi ráp thành phẩm' : 'Cần đủ thời lượng video trước khi ráp'}"` : ''}>
          🎬 Ráp Thành Phẩm
        </button>
        ${!vp.is_duration_sufficient && (vp.uploaded_videos_count || 0) > 0 ? `
          <button type="button" class="btn-warn btn-sm" onclick="confirmAssemble('${esc(itemId)}', true, this)" ${!(vp.has_thumbnail || item.has_thumbnail) ? 'disabled title="Bắt buộc có 1 ảnh thumbnail trước khi ráp thành phẩm"' : ''}>
            ⚡ Ráp Cưỡng Bức (-${fmtSec(vp.shortfall_sec)})
          </button>
        ` : ''}
      </div>
    </div>

    <!-- 6 Sticky Lazy-Loaded Sub-Flow Tabs Bar inside Job Detail Page -->
    <div class="detail-flow-tabs" id="detailFlowTabs-${esc(itemId)}" role="tablist" aria-label="6 tab lazy load xử lý công việc">
      <button type="button" role="tab" aria-selected="${activeTab === 'overview' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'overview' ? 'active' : ''}" data-flow-tab="overview" onclick="switchDetailFlowTab('overview', '${esc(itemId)}')">
        📊 1. Tổng quan & Kịch bản
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'final_video' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'final_video' ? 'active' : ''}" data-flow-tab="final_video" onclick="switchDetailFlowTab('final_video', '${esc(itemId)}')">
        🏆 2. Video Thành Phẩm
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'shots' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'shots' ? 'active' : ''}" data-flow-tab="shots" onclick="switchDetailFlowTab('shots', '${esc(itemId)}')">
        🎬 3. Lưới Shot Video (${withVideoCount}/${cl.total_shots})
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'checklist' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'checklist' ? 'active' : ''}" data-flow-tab="checklist" onclick="switchDetailFlowTab('checklist', '${esc(itemId)}')">
        📋 4. Bảng Checklist (${cl.checked_shots_count}/${cl.total_shots})
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'images' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'images' ? 'active' : ''}" data-flow-tab="images" onclick="switchDetailFlowTab('images', '${esc(itemId)}')">
        🖼️ 5. Ảnh Keyframe & Tư Liệu (${totalImgsCount})
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'upload_audio' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'upload_audio' ? 'active' : ''}" data-flow-tab="upload_audio" onclick="switchDetailFlowTab('upload_audio', '${esc(itemId)}')">
        📤 6. Tải lên & Tinh chỉnh Audio
      </button>
      <button type="button" role="tab" aria-selected="${activeTab === 'review_board' ? 'true' : 'false'}" class="detail-flow-tab ${activeTab === 'review_board' ? 'active' : ''}" data-flow-tab="review_board" onclick="switchDetailFlowTab('review_board', '${esc(itemId)}')">
        🛡 7. Review Board${rbTabBadgeHtml(itemId)}
      </button>
    </div>

    <div class="todo-body" id="detail-${esc(itemId)}">
      ${renderDetailTabPaneHtml(item, activeTab)}
    </div>
  </article>`;
}

// --- Tab 2: Dedicated Final Assembled Video On-Screen Player & Assembly Readiness ---
function renderFinalVideoTabSection(item, cl, vp) {
  const itemId = item.item_id;
  const shotsArr = Array.isArray(cl.shots) && cl.shots.length ? cl.shots : (item.shots || []);
  const withVideoCount = shotsArr.filter(s => s.has_video).length || Number(vp.uploaded_videos_count || 0);
  const expectedSec = Number(cl.expected_total_duration_sec || item.audio_duration_sec || 0);
  const generatedSec = Number(cl.generated_total_duration_sec || vp.uploaded_videos_duration_sec || 0);
  const shortfallSec = Number(vp.shortfall_sec ?? cl.remaining_duration_to_generate_sec ?? Math.max(0, expectedSec - generatedSec));
  const isReady = !!vp.is_duration_sufficient || shortfallSec <= 0.5;
  const hasThumb = !!(vp.has_thumbnail || item.has_thumbnail);
  const thumbUrl = vp.thumbnail_url || item.thumbnail_url || '';
  const canComplete = isReady && hasThumb;

  if (item.final_video_url) {
    return `
    <section class="final-video-panel" id="final-video-panel-${esc(itemId)}" style="margin-bottom:0">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="pill pill-ok">🎉 ĐÃ RÁP VIDEO THÀNH PHẨM HOÀN CHỈNH</span>
            <span class="pill pill-ghost mono">Chuẩn: ${fmtSec(expectedSec)} · Đã gen: ${fmtSec(generatedSec)} (${withVideoCount}/${cl.total_shots} shot)</span>
          </div>
          <h3 style="margin:6px 0 2px;font-size:15px">Xem Trực Tiếp Video Thành Phẩm Trên Màn Hình (final_en.mp4)</h3>
          <div style="font-size:12px;color:var(--fg-muted)">Tải theo yêu cầu (preload="none") — bấm Play hoặc mở chế độ Cinema toàn màn hình.</div>
        </div>
        <div class="final-video-actions" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 9px;border-radius:8px;border:1px solid var(--border);background:rgba(255,255,255,.04);cursor:pointer;user-select:none">
            <input type="checkbox" id="wmToggleTab-${esc(itemId)}" checked onchange="syncWatermarkToggle('${esc(itemId)}', this.checked)">
            <span>🛡️ Chữ ký mờ @StickSto</span>
          </label>
          <button type="button" class="btn-primary btn-sm" onclick="openCinemaFinalVideo('${esc(itemId)}')">
            ⛶ Xem Phóng To Toàn Màn Hình
          </button>
          <button type="button" class="btn-ok btn-sm" onclick="confirmAssemble('${esc(itemId)}', ${!isReady ? 'true' : 'false'}, this)">
            🔄 Ráp Lại Thành Phẩm
          </button>
          <button type="button" class="btn-primary btn-sm" onclick="openYoutubePublishModal('${esc(itemId)}')" ${!hasThumb ? 'disabled title="Cần có 1 ảnh thumbnail trước khi đăng YouTube"' : ''}>
            📺 Đăng lên YouTube
          </button>
          <button type="button" class="btn-ok btn-sm" onclick="openIgPublishModal('${esc(itemId)}', 'todo')">
            📲 Đăng Instagram
          </button>
        </div>
      </div>
      <div id="igPublishStatus-${esc(itemId)}"></div>
      ${renderTodoYoutubeStatusBanner(item)}
      <div class="final-video-stage">
        <video controls preload="none" playsinline src="${esc(item.final_video_url)}"></video>
      </div>
    </section>`;
  }

  return `
  <section class="panel" id="final-video-panel-${esc(itemId)}" style="margin-bottom:0">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">
          <span>🏆 Trạng Thái Ráp Video Thành Phẩm (final_en.mp4)</span>
          ${canComplete
            ? `<span class="pill pill-ok">⚡ Đủ thời lượng &amp; Thumbnail — Sẵn sàng hoàn thành</span>`
            : !hasThumb
              ? `<span class="pill pill-warn">🖼️ Thiếu Thumbnail (bắt buộc 1 ảnh)</span>`
              : `<span class="pill pill-warn">⏳ Chưa đủ thời lượng chuẩn</span>`}
        </h3>
        <div class="panel-subtitle">
          Đối chiếu độ phủ thời lượng các Shot video hiện có với Audio EN chuẩn trước khi xuất bản Video Thành Phẩm.
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 9px;border-radius:8px;border:1px solid var(--border);background:rgba(255,255,255,.04);cursor:pointer;user-select:none">
          <input type="checkbox" id="wmToggleTab-${esc(itemId)}" checked onchange="syncWatermarkToggle('${esc(itemId)}', this.checked)">
          <span>🛡️ Chữ ký mờ @StickSto</span>
        </label>
        <div style="margin:10px 0;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:rgba(255,255,255,.03);display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          ${hasThumb && thumbUrl
            ? `<img src="${esc(thumbUrl)}" alt="Video Thumbnail" style="width:128px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--border)">`
            : `<div style="width:128px;height:72px;border-radius:6px;border:1px dashed var(--border);display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--muted)">Chưa có Thumbnail</div>`}
          <div style="flex:1;min-width:220px">
            <div style="font-size:12px;font-weight:600;margin-bottom:4px">
              🖼️ Ảnh Thumbnail Video ${hasThumb ? '<span class="pill pill-ok">Đã có</span>' : '<span class="pill pill-warn">Bắt buộc</span>'}
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <input type="file" id="thumbFile-${esc(itemId)}" accept="image/*" style="font-size:11px;max-width:220px">
              <button type="button" class="btn-sm" onclick="uploadTodoThumbnail('${esc(itemId)}', this)">📤 Upload Thumbnail</button>
            </div>
          </div>
        </div>
        <button type="button" class="btn-ok btn-sm" onclick="confirmAssemble('${esc(itemId)}', false, this)" ${!canComplete ? `disabled title="${!hasThumb ? 'Bắt buộc có 1 ảnh thumbnail trước khi hoàn thành job' : 'Cần đủ thời lượng video trước khi ráp chuẩn'}"` : ''}>
          🎬 Ráp Thành Phẩm Ngay
        </button>
        ${!isReady && withVideoCount > 0 ? `
          <button type="button" class="btn-warn btn-sm" onclick="confirmAssemble('${esc(itemId)}', true, this)" ${!hasThumb ? 'disabled title="Bắt buộc có 1 ảnh thumbnail trước khi hoàn thành job"' : ''}>
            ⚡ Ráp Cưỡng Bức (Đang thiếu ${fmtSec(shortfallSec)})
          </button>
        ` : ''}
      </div>
    </div>
    <div class="job-kpi-grid" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:12px">
      <div class="kpi-box">
        <div class="k-lbl">Số Shot Đã Có Video</div>
        <div class="k-val">${withVideoCount} / ${cl.total_shots} shot</div>
        <div class="k-note">Còn thiếu ${Math.max(0, cl.total_shots - withVideoCount)} shot</div>
      </div>
      <div class="kpi-box">
        <div class="k-lbl">Thời Lượng Audio Chuẩn</div>
        <div class="k-val">${fmtSec(expectedSec)}</div>
        <div class="k-note">Mốc thời lượng đích</div>
      </div>
      <div class="kpi-box">
        <div class="k-lbl">Thời Lượng Video Đã Có</div>
        <div class="k-val" style="color:var(--accent)">${fmtSec(generatedSec)}</div>
        <div class="k-note">Đã tải lên hệ thống</div>
      </div>
      <div class="kpi-box">
        <div class="k-lbl">Thời Lượng Còn Thiếu</div>
        <div class="k-val ${shortfallSec <= 0.5 ? 'delta-ok' : 'delta-short'}">${shortfallSec <= 0.5 ? '0.00s (Đủ)' : `-${fmtSec(shortfallSec)}`}</div>
        <div class="k-note">${shortfallSec <= 0.5 ? 'Sẵn sàng ghép' : 'Cần bổ sung shot hoặc ráp cưỡng bức'}</div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:12px;background:var(--bg);border:1px dashed var(--border-strong);border-radius:var(--r)">
      <div style="font-size:12.5px;color:var(--fg-muted)">
        ${isReady
          ? '✅ Đã đủ thời lượng video. Bấm "🎬 Ráp Thành Phẩm Ngay" để hệ thống ghép hoàn chỉnh.'
          : `⚠️ Công việc này hiện còn thiếu ${fmtSec(shortfallSec)} video so với Audio EN. Chuyển sang Tab 3 (Lưới Shot) để tải thêm video hoặc bấm "⚡ Ráp Cưỡng Bức".`}
      </div>
      <button type="button" class="btn-primary btn-xs" onclick="switchDetailFlowTab('shots', '${esc(itemId)}')">
        🎬 Mở Tab 3: Lưới Shot Video (${withVideoCount}/${cl.total_shots}) →
      </button>
    </div>
  </section>`;
}

function renderFinalVideoBanner(item) {
  const cl = buildChecklistFallback(item);
  const vp = item.video_progress || {};
  return renderFinalVideoTabSection(item, cl, vp);
}

// --- Audio Player + Zero-Download Web Viewers Bar ---
function renderAudioAndWebViewersBar(item) {
  const itemId = item.item_id;
  return `
  <section class="panel" id="audio-panel-${esc(itemId)}" style="padding:12px 14px;margin-bottom:10px">
    <div class="media-bar">
      <div class="audio-player-wrap">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
          <span style="font-weight:600;font-size:13px">🔊 Nghe Trực Tiếp Bản Thu Audio EN Chuẩn <span style="white-space:nowrap">(${fmtSec(item.audio_duration_sec)})</span></span>
          <span id="audioSegmentHint-${esc(itemId)}" class="mono" style="font-size:11px;color:var(--accent)"></span>
        </div>
        <audio id="audioPlayer-${esc(itemId)}" controls preload="none" src="${esc(item.audio_url)}"></audio>
      </div>
      <div>
        <div class="web-viewers-label">
          Xem Kịch Bản, Phụ Đề & Checklist Trực Tiếp Trên Web (Không Cần Tải File)
        </div>
        <div class="web-viewers-group">
          <button type="button" class="btn-sm" onclick="openScriptViewerModal('${esc(itemId)}')">
            📄 Xem Kịch Bản & Chia Shot (.MD)
          </button>
          <button type="button" class="btn-sm" onclick="openTextAssetModal('${esc(itemId)}', 'subtitles.srt', 'Phụ Đề Chuẩn (.SRT)')">
            💬 Phụ Đề .SRT
          </button>
          <button type="button" class="btn-sm" onclick="openTextAssetModal('${esc(itemId)}', 'subtitles.ass', 'Phụ Đề Hoạt Họa (.ASS)')">
            🎨 Phụ Đề .ASS
          </button>
          <button type="button" class="btn-sm" onclick="openChecklistJsonModal('${esc(itemId)}')">
            ☑️ JSON Checklist
          </button>
          <button type="button" class="btn-sm" onclick="openSkillViewerModal('${esc(itemId)}')">
            📐 Style v2
          </button>
        </div>
      </div>
    </div>
  </section>`;
}

// --- 4-Column Shot & Video Grid (Strict Shot Index Order + Empty Placeholders + Lazy Batching) ---
function renderFourColumnShotGridSection(item, cl) {
  const itemId = item.item_id;
  const shots = (cl && Array.isArray(cl.shots) && cl.shots.length) ? cl.shots : (item.shots || []);
  const mode = gridFilterByItem[itemId] || 'all';

  const withVideoCount = shots.filter(s => s.has_video).length;
  const missingCount = shots.filter(s => !s.has_video).length;
  const driftCount = shots.filter(s => s.has_video && Math.abs(Number(s.delta_duration_sec || 0)) > 0.35).length;
  const redCount = shots.filter(s => s.board_status === 'red').length;

  const filteredShots = shots.filter(s => {
    if (mode === 'missing') return !s.has_video;
    if (mode === 'has_video') return !!s.has_video;
    if (mode === 'drift') return s.has_video && Math.abs(Number(s.delta_duration_sec || 0)) > 0.35;
    if (mode === 'red') return s.board_status === 'red';
    return true;
  });

  const limit = getShotRenderLimit(itemId, filteredShots.length);
  const visibleShots = filteredShots.slice(0, limit);
  const cardsHtml = visibleShots.map(s => renderShotSlotCard(itemId, s)).join('');

  return `
  <section class="panel" id="shot-grid-panel-${esc(itemId)}">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">
          <span>🎬 Lưới 4 Video / Dòng Theo Đúng Vị Trí Shot <span style="white-space:nowrap">(1 → ${shots.length})</span></span>
          <span class="pill pill-accent desktop-grid-pill">4 Cột · Lazy Load</span>
          ${filteredShots.length > SHOT_LAZY_BATCH_SIZE ? `
            <button type="button" class="btn-xs" id="shot-collapse-btn-${esc(itemId)}" onclick="toggleShotGridCollapse('${esc(itemId)}')">
              ${limit < filteredShots.length ? `🔼 Tải thêm (${limit}/${filteredShots.length} Shot)` : `🔽 Thu gọn (8/${filteredShots.length})`}
            </button>
          ` : ''}
        </h3>
        <div class="panel-subtitle">
          Mỗi ô tương ứng chính xác 1 Shot theo kịch bản. Video & Shot được tải dần (Lazy Load / Infinite Scroll) khi cuộn tới để tiết kiệm tài nguyên.
        </div>
      </div>
      <div class="seg-tabs">
        <button type="button" class="seg-tab ${mode === 'all' ? 'active' : ''}" onclick="setShotGridFilter('${esc(itemId)}', 'all')">
          Tất cả vị trí (${shots.length})
        </button>
        <button type="button" class="seg-tab ${mode === 'has_video' ? 'active' : ''}" onclick="setShotGridFilter('${esc(itemId)}', 'has_video')">
          Đã có video (${withVideoCount})
        </button>
        <button type="button" class="seg-tab ${mode === 'missing' ? 'active' : ''}" onclick="setShotGridFilter('${esc(itemId)}', 'missing')">
          Đang trống / Thiếu (${missingCount})
        </button>
        <button type="button" class="seg-tab ${mode === 'drift' ? 'active' : ''}" onclick="setShotGridFilter('${esc(itemId)}', 'drift')">
          Lệch thời lượng (${driftCount})
        </button>
        ${redCount > 0 ? `
        <button type="button" class="seg-tab ${mode === 'red' ? 'active' : ''}" onclick="setShotGridFilter('${esc(itemId)}', 'red')" style="border-color:var(--held);color:var(--held)">
          ⛔ Đỏ (${redCount})
        </button>` : ''}
      </div>
    </div>

    ${filteredShots.length ? `
      <div class="shot-video-grid" id="shot-grid-list-${esc(itemId)}">
        ${cardsHtml}
      </div>
      <div id="shot-sentinel-wrap-${esc(itemId)}">
        ${renderShotGridSentinelHtml(itemId, visibleShots.length, filteredShots.length)}
      </div>
    ` : `
      <div style="text-align:center;padding:28px;color:var(--fg-muted);border:1px dashed var(--border-strong);border-radius:var(--r)">
        Không có shot nào trong chế độ lọc này.
      </div>
    `}
  </section>`;
}

function renderShotSlotCard(itemId, s) {
  const idx = Number(s.index);
  const padIdx = String(idx).padStart(2, '0');
  const shotTypeLabel = SHOT_TYPE_LABELS[s.shot_type] || s.shot_type || 'Cảnh';
  const expected = Number(s.expected_duration_sec ?? s.duration_sec ?? 0);
  const actual = Number(s.actual_duration_sec ?? s.generated_duration_sec ?? 0);
  const delta = Number(s.delta_duration_sec ?? (actual - expected));
  const remaining = Number(s.remaining_duration_sec ?? Math.max(0, expected - actual));
  const hasVideo = !!s.has_video && !!s.video_url;
  const isChecked = !!s.checked;
  const posterAttr = (s.keyframe_url || s.reference_image_url) ? `poster="${esc(s.keyframe_url || s.reference_image_url)}"` : '';

  const deltaClass = !hasVideo
    ? 'delta-missing'
    : (Math.abs(delta) <= 0.35 ? 'delta-ok' : (delta > 0 ? 'delta-over' : 'delta-short'));
  const deltaText = hasVideo
    ? `${delta > 0 ? '+' : ''}${delta.toFixed(2)}s`
    : `-${expected.toFixed(2)}s`;

  if (hasVideo) {
    return `
    <div class="shot-slot-card has-video ${isChecked ? 'is-checked' : ''} ${delta < -0.35 ? 'is-shortfall' : ''} ${s.board_status === 'red' ? 'is-red' : ''}" id="shot-card-${esc(itemId)}-${idx}">
      <div class="slot-head">
        <div class="slot-title">
          <span class="mono">Shot #${padIdx}</span>
          <span class="pill pill-ghost">${esc(shotTypeLabel)}</span>
          ${getBoardStatusPill(s)}
        </div>
        <label class="slot-check-label ${isChecked ? 'checked' : ''}" title="Tick xác nhận Shot này đã gen xong cho Agent biết">
          <input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleShotCheck('${esc(itemId)}', ${idx}, this.checked)"/>
          ${isChecked ? 'Đã gen' : 'Chưa tick'}
        </label>
      </div>

      <div class="slot-stage" id="slot-stage-${esc(itemId)}-${idx}">
        <div class="video-lazy-overlay" id="vid-overlay-${esc(itemId)}-${idx}">
          <span class="spinner-ring"></span>
          <span class="video-lazy-text">Đang chờ tải video (Lazy Load)...</span>
        </div>
        <video class="shot-inline-video" controls preload="none" playsinline
          data-src="${esc(s.video_url)}#t=0.05"
          ${posterAttr}
          onloadeddata="onInlineVideoReady(this)"
          onloadedmetadata="if(this.currentTime===0){try{this.currentTime=0.05}catch(e){}};onInlineVideoReady(this)"
          onerror="onInlineVideoError(this)"
          onplay="onInlineVideoPlay(this)"></video>
        <button type="button" class="slot-expand-btn" onclick="openCinemaShotModal('${esc(itemId)}', ${idx})">
          ⛶ Xem lớn
        </button>
      </div>

      <div class="slot-metrics">
        <div class="slot-metric-cell">
          <span class="m-lbl">Dự kiến</span>
          <span class="m-val">${expected.toFixed(2)}s</span>
        </div>
        <div class="slot-metric-cell">
          <span class="m-lbl">Thực tế</span>
          <span class="m-val" style="color:var(--accent)">${actual.toFixed(2)}s</span>
        </div>
        <div class="slot-metric-cell">
          <span class="m-lbl">Lệch dự kiến</span>
          <span class="m-val ${deltaClass}">${deltaText}</span>
        </div>
      </div>

      <div class="slot-body">
        <div>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;margin-bottom:4px;flex-wrap:wrap">
            <span class="mono" style="font-size:11px;color:var(--fg-muted)">
              Audio: ${Number(s.audio_start_sec || 0).toFixed(1)}s → ${Number(s.audio_end_sec || 0).toFixed(1)}s
            </span>
            ${getDeltaPill(s)}
          </div>
          <div class="slot-narration" title="${esc(s.narration)}">${esc(s.narration)}</div>
        </div>
        <div class="slot-foot">
          <button type="button" class="btn-xs" onclick="playAudioSegment('${esc(itemId)}', ${Number(s.audio_start_sec || 0)}, ${Number(s.audio_end_sec || 0)}, ${idx})">
            🔊 Nghe câu
          </button>
          <button type="button" class="btn-xs" onclick="openCinemaShotModal('${esc(itemId)}', ${idx})">
            ▶ Xem trên màn hình
          </button>
          <button type="button" class="btn-xs" onclick="triggerQuickSlotUpload('${esc(itemId)}', ${idx})">
            🔄 Thay
          </button>
          <button type="button" class="btn-xs" onclick="toggleShotComments('${esc(itemId)}', ${idx})" title="Góp ý của các agent cho shot này (agent gen lại đọc để sửa)">
            💬 Góp ý
          </button>
          ${s.video_seq_num ? `
            <button type="button" class="btn-xs btn-danger" onclick="deleteVideo('${esc(itemId)}', ${Number(s.video_seq_num)})" title="Xóa video khỏi Shot #${padIdx}">
              🗑
            </button>
          ` : ''}
        </div>
        <div class="rb-cmt-mount" id="cmt-card-${esc(itemId)}-${idx}"></div>
      </div>
    </div>`;
  }

  // Empty Placeholder Card at Exact Shot Position
  return `
  <div class="shot-slot-card is-empty ${isChecked ? 'is-checked' : ''}" id="shot-card-${esc(itemId)}-${idx}">
    <div class="slot-head">
      <div class="slot-title">
        <span class="mono">Shot #${padIdx}</span>
        <span class="pill pill-ghost">${esc(shotTypeLabel)}</span>
      </div>
      <label class="slot-check-label ${isChecked ? 'checked' : ''}" title="Tick nếu Agent đã gen bên ngoài">
        <input type="checkbox" ${isChecked ? 'checked' : ''} onchange="toggleShotCheck('${esc(itemId)}', ${idx}, this.checked)"/>
        ${isChecked ? 'Đã tick' : 'Chưa gen'}
      </label>
    </div>

    <div class="slot-stage empty-stage">
      <span class="empty-slot-badge">TRỐNG — THIẾU SHOT #${padIdx}</span>
      <div style="font-size:12px;font-weight:500;color:var(--fg)">
        Vị trí Shot #${padIdx} đang để trống chờ video
      </div>
      <div class="mono" style="font-size:11px;color:var(--fg-muted)">
        Cần gen đoạn dài ${expected.toFixed(2)}s (${Number(s.audio_start_sec || 0).toFixed(1)}s–${Number(s.audio_end_sec || 0).toFixed(1)}s)
      </div>
      <div style="display:flex;gap:6px;margin-top:4px;flex-wrap:wrap;justify-content:center">
        <button type="button" class="btn-primary btn-xs" onclick="triggerQuickSlotUpload('${esc(itemId)}', ${idx})">
          + Tải video vào Shot #${padIdx}
        </button>
        <button type="button" class="btn-xs" onclick="openUploadTabForShot('${esc(itemId)}', ${idx})" title="Mở Tab 6 với Shot #${padIdx} đã điền sẵn (hỗ trợ gộp nhiều shot)">
          📤 Mở Tab 6 (#${padIdx})
        </button>
        ${s.reference_image_url ? `
          <button type="button" class="btn-xs" onclick="openSingleImageModal('${esc(s.reference_image_url)}', 'Ảnh tư liệu gốc — Shot #${padIdx}', '${esc(s.narration)}')">
            🖼 Xem ảnh gợi ý
          </button>
        ` : ''}
      </div>
    </div>

    <div class="slot-metrics">
      <div class="slot-metric-cell">
        <span class="m-lbl">Dự kiến</span>
        <span class="m-val">${expected.toFixed(2)}s</span>
      </div>
      <div class="slot-metric-cell">
        <span class="m-lbl">Đã gen</span>
        <span class="m-val">${actual.toFixed(2)}s</span>
      </div>
      <div class="slot-metric-cell">
        <span class="m-lbl">Cần gen thêm</span>
        <span class="m-val delta-missing">+${remaining.toFixed(2)}s</span>
      </div>
    </div>

    <div class="slot-body">
      <div class="slot-narration" title="${esc(s.narration)}">${esc(s.narration)}</div>
      <div class="slot-foot">
        <button type="button" class="btn-xs" onclick="playAudioSegment('${esc(itemId)}', ${Number(s.audio_start_sec || 0)}, ${Number(s.audio_end_sec || 0)}, ${idx})">
          🔊 Nghe mẫu (${expected.toFixed(1)}s)
        </button>
        <button type="button" class="btn-xs" onclick="toggleShotComments('${esc(itemId)}', ${idx})" title="Góp ý của các agent cho shot này">
          💬 Góp ý
        </button>
        <span class="mono" style="font-size:11px;color:var(--held);font-weight:600">
          Lệch: -${expected.toFixed(2)}s
        </span>
      </div>
      <div class="rb-cmt-mount" id="cmt-card-${esc(itemId)}-${idx}"></div>
    </div>
  </div>`;
}

function setShotGridFilter(itemId, mode) {
  gridFilterByItem[itemId] = mode;
  shotRenderLimitByItem[itemId] = SHOT_LAZY_BATCH_SIZE;
  rerenderActiveDetailTab(itemId);
}

// =====================================================================
// REVIEW BOARD (Shot Board web UI) — chấm mù 2 lượt/shot + comment per-shot
// Luật: điểm do server tính từ defect_codes (client gửi score bị từ chối);
// comment/điểm người khác bị ẩn cho tới khi caller chấm version này (C-4),
// trừ khi caller là người gen/upload hoặc manager/admin.
// =====================================================================
let RB_INBOX_CACHE = null;        // {reviewable_counts: {item_id: n}, ...}
let RB_DEFECT_CODES = null;       // [{code, deduction, fail_level, description}]
let RB_DEFECT_MAP = null;         // code -> entry
let RB_CURRENT = null;            // việc đang mở trong panel review

function rbReviewableCount(itemId) {
  return (RB_INBOX_CACHE && RB_INBOX_CACHE.reviewable_counts)
    ? RB_INBOX_CACHE.reviewable_counts[itemId] : null;
}

function rbTabBadgeHtml(itemId) {
  const n = rbReviewableCount(itemId);
  if (n == null || n <= 0) return '';
  return `<span style="background:var(--accent);color:#fff;border-radius:9px;font-size:10px;padding:1px 6px;margin-left:5px">Chấm: ${n}</span>`;
}

function rbTodoChipHtml(itemId) {
  const n = rbReviewableCount(itemId);
  if (n == null || n <= 0) return '';
  return `<span class="pill pill-accent" title="Số shot bạn đủ điều kiện chấm trên Shot Board (đã loại shot của chính bạn)">🛡 Chấm: ${n}</span>`;
}

async function rbRefreshInboxCache() {
  try {
    const r = await apiFetch('/api/v1/agent/inbox', { timeoutMs: 20000 });
    if (r.ok) {
      RB_INBOX_CACHE = await r.json();
      renderFilteredTodos();
    }
  } catch (err) {
    console.warn('[rbRefreshInboxCache] Error:', err);
  }
}

async function rbLoadDefectCodes() {
  if (RB_DEFECT_CODES) return RB_DEFECT_CODES;
  const r = await apiFetch('/api/v1/board/defect-codes', { timeoutMs: 12000 });
  if (!r.ok) throw new Error(await extractApiError(r, 'Không tải được bảng defect codes'));
  const d = await r.json();
  RB_DEFECT_CODES = d.codes || [];
  RB_DEFECT_MAP = {};
  RB_DEFECT_CODES.forEach(c => { RB_DEFECT_MAP[c.code] = c; });
  return RB_DEFECT_CODES;
}

function rbShotLocal(itemId, idx) {
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId) || {};
  const cl = buildChecklistFallback(item);
  const shots = (cl && Array.isArray(cl.shots) && cl.shots.length) ? cl.shots : (item.shots || []);
  return shots.find(s => Number(s.index) === Number(idx)) || null;
}

function rbFmtTime(ts) {
  const n = Number(ts || 0);
  if (!n) return '';
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' ' + d.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
}

function rbErrorHtml(detail, code) {
  return `<div class="rb-empty">❌ ${esc(detail || 'Lỗi không xác định')}${code ? ` <span class="mono rb-ded">(${esc(code)})</span>` : ''}</div>`;
}

function getBoardStatusPill(s) {
  if (s.board_status === 'green') return '<span class="pill pill-ok" title="Shot Board: đã đủ 2 pass từ 2 agent khác nhau">Xanh</span>';
  if (s.board_status === 'red') return '<span class="pill pill-danger" title="Shot đỏ — phải gen lại theo regen brief">Đỏ</span>';
  if (s.grandfathered) return '<span class="pill pill-ghost" title="Video cũ trước khi bật board — không bắt buộc chấm, không tính xanh">Cũ (chưa chấm)</span>';
  if (s.has_video) return '<span class="pill pill-warn" title="Chưa đủ 2 pass trên Shot Board">Chưa chấm</span>';
  return '';
}

// ---- Tab Review Board ----
function renderReviewBoardSection(item) {
  const itemId = item.item_id;
  const reviewable = rbReviewableCount(itemId);
  const meLabel = VS_ME && VS_ME.identity ? `${esc(VS_ME.identity)} · ${esc(VS_ME.scope || '?')}` : 'chưa rõ danh tính';
  return `
  <section class="panel" id="review-board-panel-${esc(itemId)}">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">
          <span>🛡 Review Board — Chấm mù 2 lượt / shot</span>
          ${reviewable != null && reviewable > 0 ? `<span class="pill pill-accent">Còn ${reviewable} việc chấm cho bạn</span>` : '<span class="pill pill-ghost">Hết việc chấm (hoặc chưa tải inbox)</span>'}
        </h3>
        <div class="panel-subtitle">
          Server nhả đúng 1 shot bạn đủ điều kiện (không phải shot bạn gen/upload, chưa chấm version này, ưu tiên shot đã có 1 pass — chấm thêm 1 lượt là xanh ngay). Điểm do server tính — bạn chỉ tick lỗi.
        </div>
      </div>
      <span class="rb-identity"><span class="cmt-avatar">${esc(VS_ME && VS_ME.identity ? VS_ME.identity.slice(0, 1).toUpperCase() : '?')}</span><span>${meLabel}</span></span>
    </div>

    <div class="rb-banner">
      <div class="rb-banner-info" id="rb-banner-info-${esc(itemId)}">Bấm "Chấm việc tiếp theo" để nhận shot cần chấm. "Việc của tôi" để nhận shot đỏ cần gen lại (kèm brief + góp ý).</div>
      <div class="rb-actions">
        <button type="button" class="btn-primary btn-sm" onclick="rbTakeReviewJob('${esc(itemId)}')">▶ Chấm việc tiếp theo</button>
        <button type="button" class="btn-sm" onclick="rbTakeMyJob('${esc(itemId)}')">📋 Việc của tôi (gen lại)</button>
      </div>
    </div>

    <div id="rb-workarea-${esc(itemId)}">
      <div class="rb-empty">Chưa có việc nào được nhận. Một lượt chấm = xem video → tick lỗi (hoặc bỏ trống nếu sạch) → Gửi.</div>
    </div>
  </section>`;
}

// ---- Nhận việc chấm (mode=review) ----
async function rbTakeReviewJob(itemId) {
  const area = document.getElementById(`rb-workarea-${itemId}`);
  const info = document.getElementById(`rb-banner-info-${itemId}`);
  if (!area) return;
  area.innerHTML = '<div class="rb-empty"><span class="spinner-ring"></span> Đang xin việc chấm từ server...</div>';
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/next?mode=review`, { timeoutMs: 15000 });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { area.innerHTML = rbErrorHtml(d.detail, d.code); return; }
    if (info) info.textContent = d.shot
      ? `Đã nhận Shot #${String(d.shot.index).padStart(2, '0')} — chấm xong sẽ tự sang việc kế tiếp.`
      : 'Hết việc chấm cho bạn lúc này.';
    if (!d.shot) {
      area.innerHTML = `<div class="rb-empty">🎉 Hết việc chấm phù hợp với bạn lúc này (${esc(d.reason || 'no_review_work')}).${d.retry_after_sec ? ` Server gợi ý thử lại sau ~${d.retry_after_sec}s.` : ''}<div style="margin-top:10px"><button type="button" class="btn-primary btn-sm" onclick="rbTakeReviewJob('${esc(itemId)}')">🔁 Thử lại</button></div></div>`;
      return;
    }
    await rbRenderReviewPanel(itemId, d.shot, area);
  } catch (err) {
    area.innerHTML = rbErrorHtml(err.message);
  }
}

// ---- Nhận việc gen theo dispatch mặc định (red_regen/empty_ready/...) ----
async function rbTakeMyJob(itemId) {
  const area = document.getElementById(`rb-workarea-${itemId}`);
  const info = document.getElementById(`rb-banner-info-${itemId}`);
  if (!area) return;
  area.innerHTML = '<div class="rb-empty"><span class="spinner-ring"></span> Đang dò việc của bạn...</div>';
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/next`, { timeoutMs: 15000 });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { area.innerHTML = rbErrorHtml(d.detail, d.code); return; }
    if (!d.shot) {
      if (info) info.textContent = 'Không còn việc nào ở mọi luồng.';
      area.innerHTML = '<div class="rb-empty">Không còn việc nào trong mọi luồng (red_regen / empty_ready / waiting_review / missing_info).</div>';
      return;
    }
    const s = d.shot;
    const padIdx = String(s.index).padStart(2, '0');
    if (info) info.textContent = `Việc của bạn: ${d.reason} — Shot #${padIdx}.`;
    if (d.reason === 'waiting_review') { await rbRenderReviewPanel(itemId, s, area); return; }
    if (d.reason === 'red_regen') { area.innerHTML = rbRegenPanelHtml(itemId, s); return; }
    if (d.reason === 'empty_ready') { area.innerHTML = rbGenReadyPanelHtml(itemId, s); return; }
    area.innerHTML = `<div class="rb-empty">Shot #${padIdx} thiếu thông tin gen (prompt/narration) — cần owner bổ sung ở Tab 1 trước khi gen.</div>`;
  } catch (err) {
    area.innerHTML = rbErrorHtml(err.message);
  }
}

// ---- Panel chấm 2 cột ----
async function rbRenderReviewPanel(itemId, shot, area) {
  const idx = Number(shot.index);
  const local = rbShotLocal(itemId, idx) || {};
  RB_CURRENT = {
    itemId, idx,
    videoHash: shot.video_hash,
    idemKey: (crypto.randomUUID ? crypto.randomUUID() : `rv-${Date.now()}-${Math.random().toString(36).slice(2)}`)
  };
  const padIdx = String(idx).padStart(2, '0');
  const videoUrl = local.video_url || '';
  const narration = shot.narration || local.narration || '';
  const expDur = Number(shot.expected_duration_sec || local.expected_duration_sec || local.duration_sec || 0);
  const visualPrompt = shot.visual_prompt || local.visual_prompt || '';

  let codesHtml = '';
  try {
    const codes = await rbLoadDefectCodes();
    codesHtml = codes.map(c => `
      <label class="rb-defect-item ${c.fail_level ? 'is-fail' : ''}">
        <input type="checkbox" class="rb-dc" data-code="${esc(c.code)}" data-fail="${c.fail_level ? '1' : ''}" data-ded="${c.deduction}" onchange="rbSyncBriefUI('${esc(itemId)}')"/>
        <span><b>${esc(c.code)}</b> ${c.fail_level ? '<span class="pill pill-danger" style="font-size:9px;padding:1px 5px">FAIL</span>' : ''}<br/>
        <span class="rb-ded">${esc(c.description)} (${c.deduction > 0 ? '−' + c.deduction : '0'})</span></span>
      </label>`).join('');
  } catch (err) {
    codesHtml = `<div class="rb-empty">Không tải được bảng defect codes: ${esc(err.message)}</div>`;
  }

  const existingReviews = rbReviewsListHtml(shot);
  const redBrief = shot.status === 'red' && shot.regen_brief ? rbBriefViewHtml(shot.regen_brief) : '';

  area.innerHTML = `
  <div class="rb-workcard" id="rb-panel-${esc(itemId)}-${idx}">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <span class="pill pill-accent mono">Shot #${padIdx}</span>
        ${getBoardStatusPill({ ...shot, has_video: true })}
        <span class="rb-ded mono">version ${shot.video_version || 1} · hash ${esc(String(shot.video_hash || '').slice(0, 12))}…</span>
      </div>
      <button type="button" class="btn-xs" onclick="rbTakeReviewJob('${esc(itemId)}')" title="Bỏ qua shot này, lấy việc khác">⏭ Bỏ qua, lấy việc khác</button>
    </div>

    <div class="rb-layout">
      <div>
        ${videoUrl ? `
          <video controls preload="metadata" playsinline src="${esc(videoUrl)}#t=0.05" style="width:100%;border-radius:10px;border:1px solid var(--border);background:#000"></video>
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
            <button type="button" class="btn-xs" onclick="playAudioSegment('${esc(itemId)}', ${Number(local.audio_start_sec || 0)}, ${Number(local.audio_end_sec || 0)}, ${idx})">🔊 Nghe câu audio chuẩn (${expDur.toFixed(1)}s)</button>
            <button type="button" class="btn-xs" onclick="openCinemaShotModal('${esc(itemId)}', ${idx})">⛶ Xem lớn</button>
          </div>` : `
          <div class="rb-empty">Không tìm được URL video cho shot này trong dữ liệu tab hiện tại — mở Tab 3 (Lưới Shot) để xem.</div>`}
        ${visualPrompt ? `<div class="rb-workcard" style="margin-top:10px"><div class="rb-ded" style="margin-bottom:4px">VISUAL PROMPT (shot phải khớp prompt này)</div><div style="font-size:12.5px">${esc(visualPrompt)}</div></div>` : ''}
        ${narration ? `<div class="rb-workcard" style="margin-top:10px"><div class="rb-ded" style="margin-bottom:4px">NARRATION</div><div style="font-size:12.5px">${esc(narration)}</div></div>` : ''}
        ${redBrief}
      </div>

      <div>
        <div style="font-weight:600;font-size:13px;margin-bottom:6px">Tick lỗi thấy được (bỏ trống nếu sạch)</div>
        <div class="rb-defect-grid">${codesHtml}</div>

        <label style="display:inline-flex;align-items:center;gap:6px;font-size:12.5px;margin-top:10px;cursor:pointer">
          <input type="checkbox" id="rb-pairing-${esc(itemId)}-${idx}" checked onchange="rbSyncBriefUI('${esc(itemId)}')"/>
          Pairing OK — keyframe khớp narration &amp; đúng vị trí câu
        </label>

        <input type="text" id="rb-note-${esc(itemId)}-${idx}" placeholder="Ghi chú ngắn (không bắt buộc)" style="width:100%;margin-top:10px;padding:7px 9px;border:1px solid var(--border);border-radius:8px;font:inherit;font-size:12.5px;background:rgba(255,255,255,.04);color:var(--fg)"/>

        <div class="rb-brief" id="rb-brief-${esc(itemId)}-${idx}">
          <div style="font-weight:700;font-size:12.5px;color:var(--held)">⛔ Việc này sẽ FAIL — bắt buộc regen brief để agent gen lại biết cách sửa</div>
          <input type="text" id="rb-brief-what-${esc(itemId)}-${idx}" placeholder="what_wrong — sai cái gì (bắt buộc)"/>
          <input type="text" id="rb-brief-do-${esc(itemId)}-${idx}" placeholder="do_instead — làm thay đổi gì (bắt buộc)"/>
          <input type="text" id="rb-brief-staging-${esc(itemId)}-${idx}" placeholder="staging_change — đổi gì trong staging (vd: camera_move: PUSH_IN -> STATIC)"/>
        </div>

        <div class="rb-preview" id="rb-preview-${esc(itemId)}-${idx}"></div>
        <button type="button" class="btn-primary btn-sm" id="rb-submit-${esc(itemId)}-${idx}" style="width:100%;margin-top:8px" onclick="rbSubmitReview('${esc(itemId)}')">✅ Gửi chấm mù (server tự tính điểm)</button>

        <div class="rb-workcard" style="margin-top:12px">
          <div style="font-weight:600;font-size:12.5px;margin-bottom:4px">Các lượt chấm hiện có trên version này</div>
          ${existingReviews}
        </div>
      </div>
    </div>

    <div class="rb-workcard" style="margin-top:12px">
      <div style="font-weight:600;font-size:12.5px">💬 Góp ý cho shot này <span class="rb-ded">(agent gen lại sẽ đọc — có thể gửi kèm cả khi chưa chấm)</span></div>
      <div id="cmt-mount-${esc(itemId)}-${idx}"><div class="cmt-meta">Đang tải góp ý...</div></div>
    </div>
  </div>`;

  rbSyncBriefUI(itemId);
  rbLoadCommentsInto(itemId, idx, `cmt-mount-${itemId}-${idx}`);
}

function rbBriefViewHtml(brief) {
  return `<div class="rb-workcard" style="border-color:rgba(220,38,38,.6);background:rgba(220,38,38,.04);margin-top:10px">
    <div style="font-weight:700;font-size:12.5px;color:var(--held);margin-bottom:6px">⛔ REGEN BRIEF (bắt buộc thực hiện khi gen lại)</div>
    <div style="font-size:12.5px"><b>what_wrong:</b> ${esc(brief.what_wrong || '')}</div>
    <div style="font-size:12.5px"><b>do_instead:</b> ${esc(brief.do_instead || '')}</div>
    <div style="font-size:12.5px"><b>staging_change:</b> ${esc(brief.staging_change || 'none')}</div>
  </div>`;
}

function rbReviewsListHtml(shot) {
  const revs = shot.reviews || [];
  if (!revs.length) return '<div class="cmt-meta">Chưa có lượt chấm nào trên version này.</div>';
  const revealed = !!shot.scores_summary;
  const list = revs.map(r => {
    const vc = r.verdict === 'pass' ? 'pill-ok' : 'pill-danger';
    const scoreHtml = revealed && r.score != null ? ` ${r.score.toFixed(2)}` : '';
    const codesHtml = revealed && Array.isArray(r.defect_codes) && r.defect_codes.length
      ? `<div class="rb-ded" style="margin-top:3px">${esc(r.defect_codes.join(', '))}</div>` : '';
    const noteHtml = r.note ? `<div style="margin-top:3px">${esc(r.note)}</div>` : '';
    return `<div class="cmt-item">
      <span class="cmt-avatar">${esc(String(r.reviewer || '?').slice(0, 1).toUpperCase())}</span>
      <div class="cmt-bubble">
        <div class="cmt-meta"><b>${esc(r.reviewer || '?')}</b><span>${rbFmtTime(r.created_at)}</span></div>
        <div><span class="pill ${vc}" style="font-size:10px;padding:1px 7px">${scoreHtml} ${esc(r.verdict || '').toUpperCase()}</span>${codesHtml}${noteHtml}</div>
      </div>
    </div>`;
  }).join('');
  const summary = revealed && shot.scores_summary
    ? `<div class="rb-ded" style="margin-top:6px">${shot.scores_summary.count} lượt · ${shot.scores_summary.distinct_reviewers} reviewer · pass ${shot.scores_summary.pass_count} · fail ${shot.scores_summary.fail_count}</div>`
    : `<div class="rb-ded" style="margin-top:6px">${revs.length} lượt chấm — điểm bị ẩn theo luật chấm mù (bạn chưa chấm version này)</div>`;
  return `<div class="cmt-thread">${list}</div>${summary}`;
}

function rbSelectedCodes() {
  return Array.from(document.querySelectorAll('input.rb-dc:checked')).map(el => el.dataset.code);
}

function rbSyncBriefUI(itemId) {
  if (!RB_CURRENT) return;
  const { idx } = RB_CURRENT;
  const ded = Array.from(document.querySelectorAll('input.rb-dc:checked'))
    .reduce((sum, el) => sum + Number(el.dataset.ded || 0), 0);
  const hasFail = Array.from(document.querySelectorAll('input.rb-dc:checked')).some(el => el.dataset.fail === '1');
  const pairing = document.getElementById(`rb-pairing-${itemId}-${idx}`);
  const pairingOk = !pairing || pairing.checked;
  const score = Math.max(0, Math.min(10, 10 - ded));
  const verdict = (!pairingOk || hasFail || score <= 9.5) ? 'fail' : 'pass';
  const briefEl = document.getElementById(`rb-brief-${itemId}-${idx}`);
  if (briefEl) briefEl.classList.toggle('open', verdict === 'fail');
  const preview = document.getElementById(`rb-preview-${itemId}-${idx}`);
  if (preview) {
    preview.innerHTML = `Dự kiến: <span class="pill ${verdict === 'pass' ? 'pill-ok' : 'pill-danger'}" style="font-size:11px;padding:1px 8px">${score.toFixed(2)} · ${verdict.toUpperCase()}</span>
      <span class="rb-ded">— server vẫn tự tính lại từ defect codes</span>`;
  }
}

async function rbSubmitReview(itemId) {
  if (!RB_CURRENT || RB_CURRENT.itemId !== itemId) return;
  const cur = RB_CURRENT;
  const btn = document.getElementById(`rb-submit-${itemId}-${cur.idx}`);
  const codes = rbSelectedCodes();
  const pairingEl = document.getElementById(`rb-pairing-${itemId}-${cur.idx}`);
  const noteEl = document.getElementById(`rb-note-${itemId}-${cur.idx}`);
  const briefEl = document.getElementById(`rb-brief-${itemId}-${cur.idx}`);
  const body = {
    defect_codes: codes,
    pairing_ok: !pairingEl || pairingEl.checked,
    reviewed_video_hash: cur.videoHash
  };
  if (noteEl && noteEl.value.trim()) body.note = noteEl.value.trim();
  if (briefEl && briefEl.classList.contains('open')) {
    const what = document.getElementById(`rb-brief-what-${itemId}-${cur.idx}`)?.value.trim();
    const instead = document.getElementById(`rb-brief-do-${itemId}-${cur.idx}`)?.value.trim();
    const staging = document.getElementById(`rb-brief-staging-${itemId}-${cur.idx}`)?.value.trim();
    if (!what || !instead) {
      showToast('❌ Regen brief cần đủ what_wrong + do_instead trước khi gửi', 'error', 5000);
      return;
    }
    body.regen_brief = { what_wrong: what, do_instead: instead, staging_change: staging || 'none' };
  }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Đang gửi chấm...'; }
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/${cur.idx}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': cur.idemKey },
      body: JSON.stringify(body),
      timeoutMs: 30000
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = d.detail || `HTTP ${r.status}`;
      if (d.code === 'already_reviewed' || d.code === 'self_review') {
        showToast(`⚠️ ${msg} — lấy việc khác`, 'info', 5000);
        await rbRefreshInboxCache();
        await rbTakeReviewJob(itemId);
        return;
      }
      showToast(`❌ Chấm thất bại: ${msg}`, 'error', 6000);
      return;
    }
    const rv = d.review || {};
    showToast(`✅ Đã chấm Shot #${String(cur.idx).padStart(2, '0')}: ${String(rv.verdict || '').toUpperCase()}${rv.score != null ? ` (${rv.score})` : ''}`, rv.verdict === 'pass' ? 'ok' : 'info', 5000);
    RB_CURRENT = null;
    await rbRefreshInboxCache();
    await rbTakeReviewJob(itemId);
  } catch (err) {
    showToast(`❌ Lỗi gửi chấm: ${err.message || err}`, 'error', 6000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✅ Gửi chấm mù (server tự tính điểm)'; }
  }
}

// ---- Panel gen lại shot đỏ (red_regen) ----
function rbRegenPanelHtml(itemId, s) {
  const idx = Number(s.index);
  const padIdx = String(idx).padStart(2, '0');
  const local = rbShotLocal(itemId, idx) || {};
  const videoUrl = local.video_url || '';
  return `
  <div class="rb-workcard" style="border-color:rgba(220,38,38,.6)">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
      <span class="pill pill-danger mono">⛔ SHOT ĐỎ #${padIdx} — VIỆC CỦA BẠN</span>
      <span class="rb-ded mono">version ${s.video_version || 1}</span>
    </div>
    ${s.regen_brief ? rbBriefViewHtml(s.regen_brief) : '<div class="rb-ded">Không có regen brief (hiếm — báo owner).</div>'}
    ${videoUrl ? `<video controls preload="metadata" playsinline src="${esc(videoUrl)}#t=0.05" style="width:100%;max-height:300px;border-radius:10px;border:1px solid var(--border);background:#000;margin-top:10px"></video>` : ''}
    <div style="margin-top:10px;font-weight:600;font-size:12.5px">💬 Góp ý của reviewer — đọc kỹ trước khi gen lại:</div>
    <div id="cmt-mount-${esc(itemId)}-${idx}"><div class="cmt-meta">Đang tải góp ý...</div></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <input type="file" id="rb-regen-file-${esc(itemId)}-${idx}" accept="video/*" style="max-width:240px"/>
      <label style="display:inline-flex;align-items:center;gap:5px;font-size:12px;cursor:pointer">
        <input type="checkbox" id="rb-regen-staging-${esc(itemId)}-${idx}" checked/> staging đã đổi
      </label>
      <button type="button" class="btn-primary btn-sm" onclick="rbUploadRegen('${esc(itemId)}', ${idx})">⬆ Upload version mới (kèm brief)</button>
    </div>
    <div class="rb-ded" style="margin-top:6px">Upload sẽ tự kèm addresses_review_id trỏ vào fail review mới nhất — luật board bắt buộc với shot đỏ.</div>
  </div>`;
}

async function rbUploadRegen(itemId, idx) {
  const fileEl = document.getElementById(`rb-regen-file-${itemId}-${idx}`);
  const stagingEl = document.getElementById(`rb-regen-staging-${itemId}-${idx}`);
  const file = fileEl && fileEl.files && fileEl.files[0];
  if (!file) { showToast('Chọn file video trước khi upload', 'error'); return; }
  // Fail review mới nhất còn hiệu lực trên version hiện tại
  let failReviewId = '';
  try {
    const sr = await apiFetch(`/api/v1/todos/${itemId}/shots/${idx}`, { timeoutMs: 12000 });
    if (sr.ok) {
      const shot = await sr.json();
      const fr = (shot.reviews || []).find(x => x.verdict === 'fail');
      if (fr) failReviewId = fr.id;
    }
  } catch (err) { /* bỏ qua — server sẽ từ chối nếu thiếu brief */ }
  const fd = new FormData();
  fd.append('file', file);
  fd.append('shot_index', String(idx));
  fd.append('covered_shot_indexes', String(idx));
  if (failReviewId) fd.append('addresses_review_id', failReviewId);
  fd.append('staging_changed', stagingEl && stagingEl.checked ? 'true' : 'false');
  showToast(`⏳ Đang upload version mới cho Shot #${String(idx).padStart(2, '0')}...`, 'info', 6000);
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/videos`, { method: 'POST', body: fd, timeoutMs: 180000 });
    if (!r.ok) throw new Error(await extractApiError(r, 'Upload shot đỏ thất bại'));
    showToast(`✅ Đã upload version mới cho Shot #${String(idx).padStart(2, '0')} — chờ 2 reviewer chấm lại`, 'ok', 6000);
    await refreshSingleItem(itemId);
    const area = document.getElementById(`rb-workarea-${itemId}`);
    if (area) area.innerHTML = '<div class="rb-empty">Đã nộp version mới — shot quay lại hàng chờ review. Bấm "Chấm việc tiếp theo" nếu bạn cũng là reviewer.</div>';
  } catch (err) {
    showToast(`❌ ${err.message || 'Upload thất bại'}`, 'error', 6000);
  }
}

// ---- Panel gen shot trống (empty_ready) ----
function rbGenReadyPanelHtml(itemId, s) {
  const idx = Number(s.index);
  const padIdx = String(idx).padStart(2, '0');
  const expDur = Number(s.expected_duration_sec || 0);
  return `
  <div class="rb-workcard">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
      <span class="pill pill-warn mono">🎬 SHOT TRỐNG #${padIdx} — CẦN GEN</span>
      <span class="rb-ded mono">dự kiến ${expDur.toFixed(2)}s (${Number(s.audio_start_sec || 0).toFixed(1)}s → ${Number(s.audio_end_sec || 0).toFixed(1)}s)</span>
    </div>
    ${s.visual_prompt ? `<div class="rb-workcard" style="margin-bottom:8px"><div class="rb-ded" style="margin-bottom:4px">VISUAL PROMPT</div><div style="font-size:12.5px">${esc(s.visual_prompt)}</div></div>` : ''}
    ${s.narration ? `<div class="rb-workcard" style="margin-bottom:8px"><div class="rb-ded" style="margin-bottom:4px">NARRATION</div><div style="font-size:12.5px">${esc(s.narration)}</div></div>` : ''}
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="btn-primary btn-sm" onclick="triggerQuickSlotUpload('${esc(itemId)}', ${idx})">⬆ Upload video vào Shot #${padIdx}</button>
      <button type="button" class="btn-xs" onclick="playAudioSegment('${esc(itemId)}', ${Number(s.audio_start_sec || 0)}, ${Number(s.audio_end_sec || 0)}, ${idx})">🔊 Nghe câu audio chuẩn</button>
    </div>
    <div class="rb-ded" style="margin-top:8px">Sau khi upload, shot vào hàng chờ review — 2 agent khác nhau chấm pass thì shot xanh.</div>
  </div>`;
}

// ---- Comment per-shot (component dùng chung) ----
function renderShotCommentsHtml(d, mountId, itemId, idx) {
  const visible = !!d.comments_visible;
  const count = Number(d.comment_count || 0);
  const hiddenNote = !visible && count > 0
    ? `<div class="cmt-meta">🔒 ${count} góp ý — ẩn cho tới khi bạn chấm version này (luật chấm mù)</div>` : '';
  const emptyNote = visible && count === 0
    ? '<div class="cmt-meta">Chưa có góp ý nào cho shot này.</div>' : '';
  const list = visible
    ? (d.comments || []).map(c => `
      <div class="cmt-item">
        <span class="cmt-avatar">${esc(String(c.author || '?').slice(0, 1).toUpperCase())}</span>
        <div class="cmt-bubble">
          <div class="cmt-meta"><b>${esc(c.author || '?')}</b><span>${rbFmtTime(c.created_at)}</span><span class="rb-ded">v${c.video_version || 1}</span></div>
          <div>${esc(c.body || '')}</div>
        </div>
      </div>`).join('')
    : '';
  return `${hiddenNote}${emptyNote}<div class="cmt-thread">${list}</div>
  <div class="cmt-input-row">
    <textarea id="cmt-input-${esc(mountId)}" placeholder="Thêm góp ý cho shot này — agent gen lại sẽ đọc..."></textarea>
    <button type="button" class="btn-primary btn-sm" onclick="rbSendComment('${esc(itemId)}', ${idx}, this, '${esc(mountId)}')">Gửi</button>
  </div>`;
}

async function rbLoadCommentsInto(itemId, idx, mountId) {
  const el = document.getElementById(mountId);
  if (!el) return;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/${idx}/comments`, { timeoutMs: 12000 });
    if (!r.ok) { el.innerHTML = `<div class="cmt-meta">Không tải được góp ý (HTTP ${r.status}).</div>`; return; }
    const d = await r.json();
    el.innerHTML = renderShotCommentsHtml(d, mountId, itemId, idx);
  } catch (err) {
    el.innerHTML = `<div class="cmt-meta">Lỗi tải góp ý: ${esc(err.message || '')}</div>`;
  }
}

async function rbSendComment(itemId, idx, btn, mountId) {
  const ta = document.getElementById(`cmt-input-${mountId}`);
  const text = ta ? (ta.value || '').trim() : '';
  if (!text) { showToast('Nhập nội dung góp ý trước khi gửi', 'error'); return; }
  if (btn) btn.disabled = true;
  const idemKey = crypto.randomUUID ? crypto.randomUUID() : `cmt-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/${idx}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey },
      body: JSON.stringify({ body: text }),
      timeoutMs: 20000
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    if (ta) ta.value = '';
    showToast('💬 Đã gửi góp ý', 'ok', 3000);
    await rbLoadCommentsInto(itemId, idx, mountId);
  } catch (err) {
    showToast(`❌ Gửi góp ý thất bại: ${err.message || err}`, 'error', 5000);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function toggleShotComments(itemId, idx) {
  const mount = document.getElementById(`cmt-card-${itemId}-${idx}`);
  if (!mount) return;
  const willOpen = !mount.classList.contains('open');
  mount.classList.toggle('open', willOpen);
  if (willOpen) await rbLoadCommentsInto(itemId, idx, `cmt-card-${itemId}-${idx}`);
}

// =====================================================================
// MUSE PROPOSALS — Inbox đề xuất Short (muse) → review chéo 3 agent
// → Telegram chờ owner duyệt → needs_script hàng chờ ưu tiên gen kịch bản.
// Owner duyệt: panel này (nút Duyệt/Từ chối) hoặc /duyet /tuchoi trên Telegram.
// =====================================================================
function renderAgentShotChecklistSection(item, cl) {
  const itemId = item.item_id;
  const shots = (cl && Array.isArray(cl.shots)) ? cl.shots : [];
  const filterMode = checklistFilterByItem[itemId] || 'all';

  const rows = shots.filter(s => {
    if (filterMode === 'unchecked') return !s.checked;
    if (filterMode === 'checked') return !!s.checked;
    if (filterMode === 'drift') return Math.abs(Number(s.delta_duration_sec || 0)) > 0.35 || !s.has_video;
    return true;
  });

  const limit = getChecklistRenderLimit(itemId, rows.length);
  const visibleRows = rows.slice(0, limit);
  const missingList = (cl.missing_shot_indexes || []).slice(0, 20).map(i => `#${i}`).join(', ');

  return `
  <section class="panel" id="checklist-panel-${esc(itemId)}">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">
          ☑️ Bảng Tick Chọn Shot & Theo Dõi Số Giây Cho Agent Gen Video
          <span class="pill pill-ok">Đã gen ${cl.checked_shots_count}/${cl.total_shots} shot</span>
          <span class="pill ${cl.remaining_duration_to_generate_sec <= 0.5 ? 'pill-ok' : 'pill-danger'}">
            Agent cần gen thêm: +${fmtSec(cl.remaining_duration_to_generate_sec)}
          </span>
          ${rows.length > CHECKLIST_LAZY_BATCH_SIZE ? `
            <button type="button" class="btn-xs" id="checklist-collapse-btn-${esc(itemId)}" onclick="toggleChecklistCollapse('${esc(itemId)}')">
              ${limit < rows.length ? `🔼 Tải thêm (${limit}/${rows.length} dòng)` : `🔽 Thu gọn bảng (12/${rows.length})`}
            </button>
          ` : ''}
        </h3>
        <div class="panel-subtitle">
          Tick chọn trực tiếp hoặc để Agent tự động đồng bộ qua trạng thái checklist. Hiển thị rõ từng shot đã gen bao nhiêu giây, lệch bao nhiêu giây và còn thiếu bao nhiêu giây.
          ${missingList ? `<br/><b>Các vị trí Shot đang trống cần Agent gen:</b> <span class="mono" style="color:var(--held)">${esc(missingList)}${(cl.missing_shot_indexes || []).length > 20 ? '...' : ''}</span>` : ''}
        </div>
      </div>
      <div class="checklist-batch-actions">
        <button type="button" class="btn-sm" onclick="batchSyncChecklist('${esc(itemId)}', {check_all_with_video: true}, this)">
          ☑ Tick tất cả shot đã có video
        </button>
        <button type="button" class="btn-sm" onclick="batchSyncChecklist('${esc(itemId)}', {uncheck_missing: true}, this)">
          ☐ Bỏ tick shot chưa có video
        </button>
        <button type="button" class="btn-sm" onclick="openChecklistJsonModal('${esc(itemId)}')">
          👁 Xem bản dữ liệu Agent gọi về
        </button>
      </div>
    </div>

    <div class="checklist-filter-bar">
      <div class="seg-tabs">
        <button type="button" class="seg-tab ${filterMode === 'all' ? 'active' : ''}" onclick="setChecklistFilter('${esc(itemId)}', 'all')">
          Tất cả (${shots.length})
        </button>
        <button type="button" class="seg-tab ${filterMode === 'unchecked' ? 'active' : ''}" onclick="setChecklistFilter('${esc(itemId)}', 'unchecked')">
          Chưa tick / Chưa gen (${cl.unchecked_shots_count})
        </button>
        <button type="button" class="seg-tab ${filterMode === 'checked' ? 'active' : ''}" onclick="setChecklistFilter('${esc(itemId)}', 'checked')">
          Đã tick (${cl.checked_shots_count})
        </button>
        <button type="button" class="seg-tab ${filterMode === 'drift' ? 'active' : ''}" onclick="setChecklistFilter('${esc(itemId)}', 'drift')">
          Thiếu hoặc lệch giây
        </button>
      </div>
      <div style="font-size:12px;color:var(--fg-muted)">
        Tổng dự kiến: <b class="mono">${fmtSec(cl.expected_total_duration_sec)}</b> ·
        Đã gen: <b class="mono" style="color:var(--accent)">${fmtSec(cl.generated_total_duration_sec)}</b> ·
        Cần gen thêm: <b class="mono" style="color:var(--held)">+${fmtSec(cl.remaining_duration_to_generate_sec)}</b>
      </div>
    </div>

    <div class="checklist-table-wrap" data-item-id="${esc(itemId)}">
      <table class="checklist-table">
        <thead>
          <tr>
            <th style="width:74px">Đã Gen</th>
            <th style="width:86px">Shot</th>
            <th style="width:100px">Kiểu Cảnh</th>
            <th style="width:125px">Mốc Audio</th>
            <th style="width:95px">Dự Kiến</th>
            <th style="width:130px">Đã Gen (Giây)</th>
            <th style="width:140px">Độ Lệch (Δ)</th>
            <th style="width:120px">Cần Gen Thêm</th>
            <th>Lời Thoại & Thao Tác Trực Tiếp</th>
          </tr>
        </thead>
        <tbody id="checklist-tbody-${esc(itemId)}">
          ${visibleRows.map(s => renderChecklistRowHtml(itemId, s)).join('')}
        </tbody>
      </table>
    </div>
    <div id="checklist-sentinel-wrap-${esc(itemId)}">
      ${renderChecklistSentinelHtml(itemId, visibleRows.length, rows.length)}
    </div>
  </section>`;
}

function setChecklistFilter(itemId, mode) {
  checklistFilterByItem[itemId] = mode;
  checklistRenderLimitByItem[itemId] = CHECKLIST_LAZY_BATCH_SIZE;
  rerenderActiveDetailTab(itemId);
}

// --- Checklist API Actions ---
async function toggleShotCheck(itemId, shotIndex, checked) {
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/${shotIndex}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ checked })
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Không thể cập nhật trạng thái Shot #${shotIndex}`));
    }
    await refreshSingleItem(itemId);
    showToast(`Đã ${checked ? 'tick hoàn tất' : 'bỏ tick'} Shot #${String(shotIndex).padStart(2, '0')}`, 'ok', 2200);
  } catch (err) {
    console.error('[toggleShotCheck] Error:', err);
    const msg = (err && err.message) ? err.message : `Không thể cập nhật trạng thái Shot #${shotIndex}`;
    showToast(msg, 'error');
    alert(msg);
    rerenderSingleCard(itemId);
  }
}

async function updateShotDuration(itemId, shotIndex, val, currentChecked) {
  const sec = Math.max(0, parseFloat(val) || 0);
  const checked = sec > 0 ? true : currentChecked;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shots/${shotIndex}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        checked,
        generated_duration_sec: sec
      })
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Lỗi lưu thời lượng Shot #${shotIndex}`));
    }
    await refreshSingleItem(itemId);
    showToast(`Đã cập nhật thời lượng Shot #${String(shotIndex).padStart(2, '0')} (${sec.toFixed(2)}s)`, 'ok', 2200);
  } catch (err) {
    console.error('[updateShotDuration] Error:', err);
    const msg = (err && err.message) ? err.message : `Lỗi lưu thời lượng Shot #${shotIndex}`;
    showToast(msg, 'error');
    alert(msg);
    rerenderSingleCard(itemId);
  }
}

async function batchSyncChecklist(itemId, payload, btnEl = null) {
  const prevText = btnEl ? btnEl.textContent : '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '⏳ Đang đồng bộ...';
  }
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/shot-checklist`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Lỗi đồng bộ bảng tick chọn'));
    }
    await refreshSingleItem(itemId);
    showToast('Đã đồng bộ bảng tick chọn Shot thành công', 'ok', 2500);
  } catch (err) {
    console.error('[batchSyncChecklist] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi đồng bộ bảng tick chọn';
    showToast(msg, 'error');
    alert(msg);
  } finally {
    if (btnEl && btnEl.isConnected) {
      btnEl.disabled = false;
      btnEl.textContent = prevText;
    }
  }
}

async function refreshSingleItem(itemId, notify = false, btnEl = null) {
  const prevText = btnEl ? btnEl.textContent : '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '⏳ Đang đồng bộ...';
  }
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}`, { timeoutMs: 20000 });
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Không thể đồng bộ dữ liệu bài ${itemId}`));
    }
    const fresh = await r.json();
    detailsCache[itemId] = fresh;
    const idx = allTodos.findIndex(x => x.item_id === itemId);
    if (idx >= 0) {
      allTodos[idx] = { ...allTodos[idx], ...fresh };
    }
    updateOverviewStats();
    renderJobDirectory();
    rerenderSingleCard(itemId);
    if (currentModalContext && currentModalContext.type === 'cinema_shot' && currentModalContext.itemId === itemId) {
      syncCinemaModalShotState(itemId, currentModalContext.shotIndex);
    }
    if (notify) {
      showToast('Đã đồng bộ tiến độ Shot mới nhất', 'ok', 2200);
    }
  } catch (err) {
    console.error('[refreshSingleItem] Error:', err);
    const msg = (err && err.message) ? err.message : `Lỗi khi đồng bộ công việc ${itemId}`;
    showToast(msg, 'error');
  } finally {
    if (btnEl && btnEl.isConnected) {
      btnEl.disabled = false;
      btnEl.textContent = prevText;
    }
  }
}

// --- Quick 1-Click Upload for Empty Shot Slot ---
function triggerQuickSlotUpload(itemId, shotIndex) {
  quickUploadTarget = { itemId, shotIndex };
  const inp = document.getElementById('quickSlotVideoInput');
  inp.value = '';
  inp.click();
}

async function handleQuickSlotVideoSelected(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file || !quickUploadTarget) return;
  const { itemId, shotIndex } = quickUploadTarget;
  const fd = new FormData();
  fd.append('file', file);
  // B2: upload bắt buộc gắn shot — quick-upload luôn gắn đúng shot của nó
  fd.append('shot_index', String(shotIndex));
  fd.append('seq_num', String(shotIndex));
  fd.append('covered_shot_indexes', JSON.stringify([shotIndex]));
  fd.append('note', `Uploaded directly into Shot #${shotIndex}`);

  showToast(`⏳ Đang tải video lên Shot #${String(shotIndex).padStart(2, '0')}...`, 'info', 4000);
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/videos`, {
      method: 'POST',
      body: fd,
      timeoutMs: 120000
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Tải video thất bại'));
    }
    await refreshSingleItem(itemId);
    showToast(`✅ Đã tải video vào Shot #${String(shotIndex).padStart(2, '0')} thành công`, 'ok');
  } catch (err) {
    console.error('[handleQuickSlotVideoSelected] Error:', err);
    const msg = (err && err.message) ? err.message : 'Tải video thất bại';
    showToast(msg, 'error');
    alert('Tải video thất bại: ' + msg);
  }
}

// --- Manual Upload Section (Supports Single or Multi-Shot Video + Keyframe Upload) ---
function renderUploadControlsSection(item) {
  const itemId = item.item_id;
  return `
  <section class="panel" id="upload-panel-${esc(itemId)}">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">📤 Tải Lên Video Shot Hoặc Ảnh Keyframe</h3>
        <div class="panel-subtitle">Có thể bấm "+ Tải video vào Shot #..." ngay trên từng ô trống ở Tab 3 (Lưới Shot), hoặc tải lên theo vị trí / gộp nhiều Shot tại đây.</div>
      </div>
    </div>
    <div class="upload-forms-grid">
      <form id="upVideoForm-${esc(itemId)}" onsubmit="uploadVideoForm(event, '${esc(itemId)}')" class="upload-form-box">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px">🎬 Tải Video Vào Đúng Vị Trí Shot</div>
        <div class="upload-form-controls">
          <input type="file" id="upVideoFile-${esc(itemId)}" name="file" accept="video/*" required style="max-width:220px"/>
          <input type="number" id="upShotNum-${esc(itemId)}" name="seq_num" placeholder="Số Shot (VD: 3)" min="1" style="width:130px"/>
          <input type="text" id="upCoveredShots-${esc(itemId)}" name="covered_shot_indexes" placeholder="Gộp shot (VD: [3,4])" style="width:145px"/>
          <button type="submit" class="btn-primary btn-sm">Tải Video Lên</button>
        </div>
      </form>

      <form id="upImageForm-${esc(itemId)}" onsubmit="uploadImageForm(event, '${esc(itemId)}')" class="upload-form-box">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px">🖼 Tải Ảnh Keyframe / Nhân Vật</div>
        <div class="upload-form-controls">
          <input type="file" id="upImgFile-${esc(itemId)}" name="file" accept="image/*" required style="max-width:210px"/>
          <select id="upImgKind-${esc(itemId)}" name="kind" style="width:140px">
            <option value="keyframe">Keyframe Shot</option>
            <option value="character">Character Sheet</option>
            <option value="scene">Scene Illustration</option>
            <option value="reference">Style Reference</option>
          </select>
          <input type="number" id="upImgShotNum-${esc(itemId)}" name="shot_index" placeholder="Shot #" min="1" style="width:90px"/>
          <button type="submit" class="btn-sm">Tải Ảnh Lên</button>
        </div>
      </form>
    </div>
  </section>`;
}

async function uploadVideoForm(ev, itemId) {
  ev.preventDefault();
  const f = ev.target;
  const submitBtn = f.querySelector('button[type="submit"]');
  const prevBtnText = submitBtn ? submitBtn.textContent : '';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ Đang tải lên...';
  }
  try {
    const fd = new FormData();
    fd.append('file', f.file.files[0]);
    if (f.seq_num.value) {
      fd.append('seq_num', f.seq_num.value);
      if (!f.covered_shot_indexes.value) {
        // B2: gắn board trực tiếp cho 1 shot
        fd.append('shot_index', f.seq_num.value);
        fd.append('covered_shot_indexes', JSON.stringify([Number(f.seq_num.value)]));
      }
    }
    if (f.covered_shot_indexes.value) {
      fd.append('covered_shot_indexes', f.covered_shot_indexes.value);
    }
    const r = await apiFetch(`/api/v1/todos/${itemId}/videos`, { method: 'POST', body: fd, timeoutMs: 120000 });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Lỗi tải video lên máy chủ'));
    }
    f.reset();
    await refreshSingleItem(itemId);
    showToast('✅ Đã tải video lên thành công', 'ok');
  } catch (err) {
    console.error('[uploadVideoForm] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi tải video';
    showToast(msg, 'error');
    alert('Lỗi tải video: ' + msg);
  } finally {
    if (submitBtn && submitBtn.isConnected) {
      submitBtn.disabled = false;
      submitBtn.textContent = prevBtnText || 'Tải Video Lên';
    }
  }
}

async function uploadImageForm(ev, itemId) {
  ev.preventDefault();
  const f = ev.target;
  const submitBtn = f.querySelector('button[type="submit"]');
  const prevBtnText = submitBtn ? submitBtn.textContent : '';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ Đang tải lên...';
  }
  try {
    const fd = new FormData();
    fd.append('file', f.file.files[0]);
    fd.append('kind', f.kind.value);
    if (f.shot_index.value) fd.append('shot_index', f.shot_index.value);
    const r = await apiFetch(`/api/v1/todos/${itemId}/images`, { method: 'POST', body: fd, timeoutMs: 90000 });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Lỗi tải ảnh lên máy chủ'));
    }
    f.reset();
    await refreshSingleItem(itemId);
    showToast('✅ Đã tải ảnh lên thành công', 'ok');
  } catch (err) {
    console.error('[uploadImageForm] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi tải ảnh';
    showToast(msg, 'error');
    alert('Lỗi tải ảnh: ' + msg);
  } finally {
    if (submitBtn && submitBtn.isConnected) {
      submitBtn.disabled = false;
      submitBtn.textContent = prevBtnText || 'Tải Ảnh Lên';
    }
  }
}

async function deleteVideo(itemId, seqNum) {
  if (!confirm(`Xóa video ở vị trí #${seqNum}? Ô này sẽ trở về trạng thái Trống.`)) return;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/videos/${seqNum}`, { method: 'DELETE' });
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Không thể xóa video #${seqNum}`));
    }
    await refreshSingleItem(itemId);
    showToast(`Đã xóa video #${seqNum}`, 'ok', 2200);
  } catch (err) {
    console.error('[deleteVideo] Error:', err);
    const msg = (err && err.message) ? err.message : `Lỗi khi xóa video #${seqNum}`;
    showToast(msg, 'error');
    alert(msg);
  }
}

async function deleteImage(itemId, seqNum, kind = 'keyframe') {
  if (!confirm(`Xóa ảnh ${kind} #${seqNum}?`)) return;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/images/${seqNum}?kind=${encodeURIComponent(kind || 'keyframe')}`, { method: 'DELETE' });
    if (!r.ok) {
      throw new Error(await extractApiError(r, `Không thể xóa ảnh ${kind} #${seqNum}`));
    }
    await refreshSingleItem(itemId);
    showToast(`Đã xóa ảnh ${kind} #${seqNum}`, 'ok', 2200);
  } catch (err) {
    console.error('[deleteImage] Error:', err);
    const msg = (err && err.message) ? err.message : `Lỗi khi xóa ảnh ${kind} #${seqNum}`;
    showToast(msg, 'error');
    alert(msg);
  }
}

function syncWatermarkToggle(itemId, checked) {
  const topEl = document.getElementById(`wmToggle-${itemId}`);
  const tabEl = document.getElementById(`wmToggleTab-${itemId}`);
  if (topEl) topEl.checked = !!checked;
  if (tabEl) tabEl.checked = !!checked;
}

async function uploadTodoThumbnail(itemId, btnEl = null) {
  const input = document.getElementById(`thumbFile-${itemId}`);
  const file = input && input.files && input.files[0];
  if (!file) {
    showToast('⚠️ Vui lòng chọn 1 file ảnh thumbnail trước khi upload.', 'warn', 3500);
    return;
  }
  const prevText = btnEl ? btnEl.textContent : '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '⏳ Đang tải lên...';
  }
  try {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('kind', 'thumbnail');
    fd.append('seq_num', '1');
    const r = await apiFetch(`/api/v1/todos/${itemId}/images`, {
      method: 'POST',
      body: fd,
      timeoutMs: 60000
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || d.message || `HTTP ${r.status}`);
    showToast('✅ Đã upload ảnh thumbnail cho job!', 'ok', 3500);
    await refreshSingleItem(itemId, false);
  } catch (e) {
    showToast(`❌ Lỗi upload thumbnail: ${e.message}`, 'err', 5000);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = prevText;
    }
  }
}

async function confirmAssemble(itemId, allowShortfall, btnEl = null) {
  const prevText = btnEl ? btnEl.textContent : '';
  const wmEl = document.getElementById(`wmToggle-${itemId}`) || document.getElementById(`wmToggleTab-${itemId}`);
  const watermarkEnabled = wmEl ? !!wmEl.checked : true;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '⏳ Đang ghép video...';
  }
  showToast('⏳ Đang tiến hành ghép video thành phẩm, vui lòng chờ...', 'info', 5000);
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/confirm-assemble`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        burn_subtitles: false,
        allow_shortfall: !!allowShortfall,
        watermark_enabled: watermarkEnabled,
        watermark_text: '@StickSto'
      }),
      timeoutMs: 300000
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không thể ráp video'));
    }
    await refreshSingleItem(itemId);
    showToast('🎉 Đã ráp video thành phẩm hoàn chỉnh!', 'ok');
  } catch (err) {
    console.error('[confirmAssemble] Error:', err);
    const msg = (err && err.message) ? err.message : 'Không thể ráp video';
    showToast(msg, 'error');
    alert('Không thể ráp video: ' + msg);
  } finally {
    if (btnEl && btnEl.isConnected) {
      btnEl.disabled = false;
      btnEl.textContent = prevText;
    }
  }
}

// --- Smart Image Fallback (Recovers SVG-in-PNG & Replaces Non-Image Media with Clean Placeholder) ---
async function handleImgError(imgEl) {
  if (!imgEl) return;
  const origSrc = imgEl.getAttribute('src') || '';
  if (!origSrc || origSrc === TRANSPARENT_PIXEL) return;
  if (imgEl.dataset.fallbackHandled === '1') return;
  imgEl.dataset.fallbackHandled = '1';
  onLazyImgLoaded(imgEl);
  if (!origSrc.startsWith('data:') && !origSrc.startsWith('blob:')) {
    try {
      const r = await fetch(origSrc, { credentials: 'same-origin' });
      if (r.ok) {
        const buf = await r.arrayBuffer();
        const head = new TextDecoder('utf-8', { fatal: false }).decode(buf.slice(0, 256)).trim();
        if (head.startsWith('<?xml') || head.startsWith('<svg') || head.includes('<svg')) {
          const blob = new Blob([buf], { type: 'image/svg+xml' });
          imgEl.src = URL.createObjectURL(blob);
          return;
        }
      }
    } catch (e) {}
  }
  const label = imgEl.getAttribute('alt') || 'Tư liệu tham khảo';
  const box = document.createElement('div');
  box.className = 'img-fallback-box';
  box.innerHTML = `<span style="font-size:18px">🖼</span><span>${esc(label)}</span>`;
  const wrap = imgEl.closest('.img-lazy-wrap');
  if (wrap) {
    wrap.replaceWith(box);
  } else {
    imgEl.replaceWith(box);
  }
}

// --- Visual Assets Section (Keyframes & Reference Images with Lazy Load + Infinite Scroll + Lightbox) ---
function renderUploadedImageCardHtml(itemId, img, i) {
  return `
  <div class="img-card" role="button" tabindex="0" data-kb-click="1" onclick="openGalleryLightbox('${esc(itemId)}', 'uploaded', ${i})">
    <div class="img-lazy-wrap">
      <div class="img-lazy-loader">
        <span class="spinner-ring"></span>
        <span>Đang tải ảnh...</span>
      </div>
      <img class="lazy-img" src="${TRANSPARENT_PIXEL}" data-src="${esc(img.file_url)}" decoding="async" alt="Keyframe #${img.seq_num}" onload="if(this.src!==TRANSPARENT_PIXEL)onLazyImgLoaded(this)" onerror="handleImgError(this)"/>
    </div>
    <div class="img-card-body">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b class="mono">#${img.seq_num} ${img.shot_index ? `(Shot #${img.shot_index})` : ''}</b>
        <button type="button" class="btn-xs btn-danger" onclick="event.stopPropagation();deleteImage('${esc(itemId)}', ${img.seq_num}, '${esc(img.kind || 'keyframe')}')">✕</button>
      </div>
      <div style="color:var(--fg-muted)">${esc(img.kind)} · ${img.width}×${img.height}</div>
    </div>
  </div>`;
}

function renderRefImageCardHtml(itemId, ref, i) {
  return `
  <div class="img-card" role="button" tabindex="0" data-kb-click="1" onclick="openGalleryLightbox('${esc(itemId)}', 'reference', ${i})">
    <div class="img-lazy-wrap">
      <div class="img-lazy-loader">
        <span class="spinner-ring"></span>
        <span>Đang tải ảnh...</span>
      </div>
      <img class="lazy-img" src="${TRANSPARENT_PIXEL}" data-src="${esc(ref.file_url)}" decoding="async" alt="Ref Shot #${ref.shot_index}" onload="if(this.src!==TRANSPARENT_PIXEL)onLazyImgLoaded(this)" onerror="handleImgError(this)"/>
    </div>
    <div class="img-card-body">
      <b class="mono" style="color:var(--accent)">Shot #${ref.shot_index}${ref.timestamp_sec != null ? ` (${fmtSec(ref.timestamp_sec)})` : ''}</b>
      <div style="color:var(--fg-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(ref.search_query || ref.title || ref.name || ref.prompt_used || '')}</div>
    </div>
  </div>`;
}

function renderImagesSentinelHtml(itemId, group, visibleCount, totalCount) {
  if (visibleCount >= totalCount) return '';
  const rem = totalCount - visibleCount;
  const nextStep = Math.min(IMG_LAZY_BATCH_SIZE, rem);
  const kind = group === 'uploaded' ? 'uploaded_imgs' : 'ref_imgs';
  return `
    <div class="lazy-section-sentinel" id="${group}-img-sentinel-${esc(itemId)}" data-lazy-kind="${kind}" data-item-id="${esc(itemId)}" data-auto="1">
      <div class="infinite-sentinel-status">
        <span class="spinner-ring"></span>
        <span>Đang hiển thị ${visibleCount} / ${totalCount} ảnh — Cuộn xuống tự động tải thêm...</span>
      </div>
      <div class="infinite-sentinel-actions">
        <button type="button" class="btn-primary btn-xs" onclick="loadMoreImagesBatch('${esc(itemId)}', '${group}', ${IMG_LAZY_BATCH_SIZE})">
          ↓ Tải tiếp ${nextStep} ảnh (Còn ${rem})
        </button>
      </div>
    </div>`;
}

function loadMoreImagesBatch(itemId, group, batchSize = IMG_LAZY_BATCH_SIZE) {
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId) || {};
  const list = group === 'uploaded' ? (item.uploaded_images || []) : (item.reference_images || []);
  const limitMap = group === 'uploaded' ? uploadedImgLimitByItem : refImgLimitByItem;
  const oldLimit = Math.min(list.length, limitMap[itemId] || IMG_LAZY_BATCH_SIZE);
  const newLimit = Math.min(list.length, oldLimit + batchSize);
  limitMap[itemId] = newLimit;

  const gridEl = document.getElementById(`${group}-img-grid-${itemId}`);
  const sentinelWrap = document.getElementById(`${group}-img-sentinel-wrap-${itemId}`);
  if (!gridEl || newLimit <= oldLimit) return;

  const slice = list.slice(oldLimit, newLimit);
  const html = slice.map((img, idx) =>
    group === 'uploaded'
      ? renderUploadedImageCardHtml(itemId, img, oldLimit + idx)
      : renderRefImageCardHtml(itemId, img, oldLimit + idx)
  ).join('');
  gridEl.insertAdjacentHTML('beforeend', html);
  if (sentinelWrap) {
    sentinelWrap.innerHTML = renderImagesSentinelHtml(itemId, group, newLimit, list.length);
  }
  setupLazyImageObserver(gridEl);
  if (sentinelWrap) setupSectionLazyObserver(sentinelWrap);
}

function handleRefImagesToggle(itemId, detailsEl) {
  if (!detailsEl) return;
  refDetailsOpenByItem[itemId] = detailsEl.open;
  if (!detailsEl.open) return;
  const bodyWrap = document.getElementById(`ref-imgs-lazy-body-${itemId}`);
  if (!bodyWrap || bodyWrap.dataset.mounted === '1') return;
  bodyWrap.dataset.mounted = '1';
  const item = detailsCache[itemId] || allTodos.find(t => t.item_id === itemId) || {};
  const refImgs = item.reference_images || [];
  const limit = Math.min(refImgs.length, refImgLimitByItem[itemId] || IMG_LAZY_BATCH_SIZE);
  refImgLimitByItem[itemId] = limit;
  const visibleRefs = refImgs.slice(0, limit);
  bodyWrap.innerHTML = `
    <div class="img-grid" id="reference-img-grid-${esc(itemId)}" style="margin-top:10px">
      ${visibleRefs.map((ref, i) => renderRefImageCardHtml(itemId, ref, i)).join('')}
    </div>
    <div id="reference-img-sentinel-wrap-${esc(itemId)}">
      ${renderImagesSentinelHtml(itemId, 'reference', visibleRefs.length, refImgs.length)}
    </div>`;
  setupLazyImageObserver(bodyWrap);
  setupSectionLazyObserver(bodyWrap);
}

function renderVisualAssetsSection(item, defaultRefOpen = false) {
  const itemId = item.item_id;
  const uploadedImgs = item.uploaded_images || [];
  const refImgs = item.reference_images || [];
  if (!uploadedImgs.length && !refImgs.length) return '';

  const upLimit = Math.min(uploadedImgs.length, uploadedImgLimitByItem[itemId] || IMG_LAZY_BATCH_SIZE);
  const visibleUploaded = uploadedImgs.slice(0, upLimit);
  const isRefOpen = refDetailsOpenByItem[itemId] !== undefined
    ? !!refDetailsOpenByItem[itemId]
    : !!defaultRefOpen;
  if (isRefOpen) {
    refDetailsOpenByItem[itemId] = true;
  }
  const refLimit = Math.min(refImgs.length, refImgLimitByItem[itemId] || IMG_LAZY_BATCH_SIZE);
  const visibleRefs = isRefOpen ? refImgs.slice(0, refLimit) : [];

  return `
  <section class="panel" id="assets-panel-${esc(itemId)}" style="margin-bottom:0">
    <div class="panel-header">
      <div>
        <h3 class="panel-title">🖼 Ảnh Keyframe Đã Gen (${uploadedImgs.length}) & Ảnh Tư Liệu Thực Tế (${refImgs.length})</h3>
        <div class="panel-subtitle">Tải ảnh thông minh (Lazy Load 12 ảnh/lần + Infinite Scroll) khi cuộn tới. Bấm vào bất kỳ ảnh nào để phóng to trên màn hình (Lightbox).</div>
      </div>
      <button type="button" class="btn-xs" onclick="openUploadTabForImage('${esc(itemId)}')">
        📤 + Tải thêm Ảnh Keyframe (Tab 6)
      </button>
    </div>

    ${uploadedImgs.length ? `
      <div style="font-size:12px;font-weight:600;margin-bottom:8px">Ảnh Keyframe / Character Sheet Đã Tạo (${uploadedImgs.length})</div>
      <div class="img-grid" id="uploaded-img-grid-${esc(itemId)}" style="margin-bottom:12px">
        ${visibleUploaded.map((img, i) => renderUploadedImageCardHtml(itemId, img, i)).join('')}
      </div>
      <div id="uploaded-img-sentinel-wrap-${esc(itemId)}" style="margin-bottom:16px">
        ${renderImagesSentinelHtml(itemId, 'uploaded', visibleUploaded.length, uploadedImgs.length)}
      </div>
    ` : ''}

    ${refImgs.length ? `
      <details ${isRefOpen ? 'open' : ''} ontoggle="handleRefImagesToggle('${esc(itemId)}', this)">
        <summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--accent)">
          🔎 Xem ${refImgs.length} Ảnh Tư Liệu Thực Tế (Visual Anchors cho Agent — Lazy Load 12 ảnh/lần)
        </summary>
        <div id="ref-imgs-lazy-body-${esc(itemId)}" data-mounted="${isRefOpen ? '1' : '0'}">
          ${isRefOpen ? `
            <div class="img-grid" id="reference-img-grid-${esc(itemId)}" style="margin-top:10px">
              ${visibleRefs.map((ref, i) => renderRefImageCardHtml(itemId, ref, i)).join('')}
            </div>
            <div id="reference-img-sentinel-wrap-${esc(itemId)}">
              ${renderImagesSentinelHtml(itemId, 'reference', visibleRefs.length, refImgs.length)}
            </div>
          ` : `
            <div class="loading-panel" style="padding:16px;margin-top:10px">
              <span class="spinner-ring"></span>
              <span style="margin-left:8px;font-size:12px;font-weight:600">Đang khởi tạo danh sách ${refImgs.length} ảnh tư liệu...</span>
            </div>
          `}
        </div>
      </details>
    ` : ''}
  </section>`;
}

// --- Audio Feedback & Regeneration Section (Clean & Compact) ---
function renderFeedbackAndRegenSection(item, defaultOpen = false) {
  const itemId = item.item_id;
  const fbs = item.agent_feedbacks || item.audio_feedbacks || [];
  return `
  <details class="panel" ${defaultOpen ? 'open' : ''} style="padding:12px 16px;margin-bottom:0">
    <summary style="cursor:pointer;font-weight:600;font-size:13px">
      🗣 Nhận Xét Bản Thu Audio (${fbs.length}) & Tinh Chỉnh Nhịp Đọc / Nhạc Nền
    </summary>
    <div class="feedback-regen-grid">
      <div>
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">Danh sách góp ý phát âm / tên riêng (${fbs.length})</div>
        ${fbs.length ? fbs.map(fb => `
          <div style="border:1px solid var(--border);border-radius:var(--r);padding:8px 10px;margin-bottom:6px;font-size:12px;background:var(--bg)">
            <div style="display:flex;justify-content:space-between;gap:6px">
              <b>[${esc(fb.category)}] ${fb.shot_index ? `Shot #${fb.shot_index}` : ''}</b>
              <span class="pill pill-ghost">${esc(FEEDBACK_STATUS_LABELS[fb.status] || fb.status)}</span>
            </div>
            <div style="margin-top:3px">${esc(fb.message)}</div>
          </div>
        `).join('') : `<div style="font-size:12px;color:var(--fg-muted)">Chưa có góp ý nào cho bản thu này.</div>`}
      </div>
      <div>
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">Tạo lại Audio EN (Tinh chỉnh tốc độ đọc & trộn nhạc nền)</div>
        <div class="regen-controls-row">
          <label>Hệ số tốc độ:
            <input type="number" id="slow-${esc(itemId)}" value="1.00" min="0.75" max="1.30" step="0.01" style="width:70px"/>
          </label>
          <label>Nghỉ thêm (giây):
            <input type="number" id="pause-${esc(itemId)}" value="0.05" min="0.0" max="0.8" step="0.01" style="width:70px"/>
          </label>
          <button type="button" class="btn-sm" onclick="regenerateAudio('${esc(itemId)}', this)">🎚 Tạo Lại Audio</button>
        </div>
        <div id="regenStatus-${esc(itemId)}" style="font-size:12px;margin-top:6px;color:var(--fg-muted)"></div>
      </div>
    </div>
  </details>`;
}

async function regenerateAudio(itemId, btnEl = null) {
  const slow = parseFloat(document.getElementById(`slow-${itemId}`).value || '1.00');
  const pause = parseFloat(document.getElementById(`pause-${itemId}`).value || '0.05');
  const st = document.getElementById(`regenStatus-${itemId}`);
  if (st) st.textContent = '⏳ Đang tổng hợp lại giọng đọc và cập nhật thời lượng từng shot...';
  if (btnEl) btnEl.disabled = true;
  try {
    const r = await apiFetch(`/api/v1/todos/${itemId}/regenerate-audio`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slow_factor: slow, extra_pause_sec: pause, bgm_enabled: true, bgm_duck_db: -20.0 }),
      timeoutMs: 180000
    });
    if (!r.ok) {
      throw new Error(await extractApiError(r, 'Không thể tạo lại Audio'));
    }
    const d = await r.json();
    if (st) st.textContent = `✅ Đã cập nhật Audio mới (${fmtSec(d.audio_duration_sec ?? d.new_duration_sec)})`;
    await refreshSingleItem(itemId);
    showToast('✅ Đã tạo lại Audio EN thành công', 'ok');
  } catch (err) {
    console.error('[regenerateAudio] Error:', err);
    const msg = (err && err.message) ? err.message : 'Lỗi khi tạo lại Audio';
    if (st) st.textContent = '❌ Lỗi: ' + msg;
    showToast(msg, 'error');
  } finally {
    if (btnEl && btnEl.isConnected) btnEl.disabled = false;
  }
}

// --- Play Specific Shot Audio Segment ---
let audioStopTimer = null;
function playAudioSegment(itemId, startSec, endSec, shotIdx) {
  const player = document.getElementById(`audioPlayer-${itemId}`);
  const hint = document.getElementById(`audioSegmentHint-${itemId}`);
  if (!player) return;
  if (audioStopTimer) clearTimeout(audioStopTimer);
  try {
    player.currentTime = Math.max(0, Number(startSec || 0));
    const p = player.play();
    if (p && typeof p.catch === 'function') {
      p.catch(err => console.warn('[playAudioSegment] play blocked:', err));
    }
  } catch (err) {
    console.warn('[playAudioSegment] Error:', err);
  }
  if (hint) {
    hint.textContent = `Đang phát Shot #${String(shotIdx).padStart(2, '0')} (${startSec.toFixed(2)}s → ${endSec.toFixed(2)}s)`;
  }
  const durMs = Math.max(500, (Number(endSec) - Number(startSec)) * 1000);
  audioStopTimer = setTimeout(() => {
    try { player.pause(); } catch (_) {}
  }, durMs + 150);
}

// --- Universal In-Page Web Viewer Modal ---
let lastModalCopyText = '';

function copyModalText(btn) {
  if (!lastModalCopyText) return;
  navigator.clipboard.writeText(lastModalCopyText).then(() => {
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = '✅ Đã sao chép';
      setTimeout(() => { btn.textContent = prev; }, 1500);
    }
  }).catch(() => {});
}

