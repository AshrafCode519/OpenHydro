// OpenHydro Modern Dashboard Controller

let tasks = {};
const renderedRows = new Map();  // task id -> { el, html }: the rows currently in the list
let selectedTaskId = null;  // the download the keyboard acts on (click or arrow keys)
let currentCategory = 'all';
let currentSearch = '';
let ws = null;
let probeTimeout = null;

// --- Modern Toast & Notification System (Replaces ugly browser alert) ---
function showToast(message, type = 'success', duration = 3500) {
  let container = document.getElementById('openhydro-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'openhydro-toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `app-toast app-toast-${type}`;

  let icon = '⚡';
  if (type === 'success') icon = '✓';
  else if (type === 'error') icon = '⚠️';
  else if (type === 'info') icon = 'ℹ️';
  else if (type === 'warning') icon = '🔔';

  toast.innerHTML = `
    <div class="flex items-center gap-3 overflow-hidden">
      <span class="app-toast-icon">${icon}</span>
      <span class="truncate text-right leading-snug">${escapeHtml(message)}</span>
    </div>
    <button class="app-toast-close" title="إغلاق">✕</button>
  `;

  const closeBtn = toast.querySelector('.app-toast-close');
  const dismiss = () => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-15px) scale(0.95)';
    setTimeout(() => toast.remove(), 250);
  };

  closeBtn.addEventListener('click', dismiss);
  container.appendChild(toast);

  setTimeout(dismiss, duration);
}

// Override default window.alert globally
window.alert = function (msg) {
  showToast(msg, 'info');
};

// --- Modern Confirmation Modal System (Replaces ugly browser confirm) ---
let confirmCallback = null;

// onOk receives the state of the optional checkbox (shown only when checkboxLabel is given)
// Danger (red, delete) or a plain question (cyan): the icon and OK button match what OK does
const CONFIRM_STYLES = {
  danger: {
    icon: 'w-14 h-14 mx-auto mb-4 rounded-2xl bg-rose-500/20 border border-rose-500/30 flex items-center justify-center text-2xl text-rose-400 shadow-lg shadow-rose-500/20',
    ok: 'flex-1 py-2.5 px-4 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-rose-500 to-red-600 hover:from-rose-400 hover:to-red-500 shadow-lg shadow-rose-500/25 transition-all',
  },
  question: {
    icon: 'w-14 h-14 mx-auto mb-4 rounded-2xl bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-2xl text-cyan-400 shadow-lg shadow-cyan-500/20',
    ok: 'flex-1 py-2.5 px-4 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 shadow-lg shadow-cyan-500/25 transition-all',
  },
};

function showConfirm({ title = 'تأكيد الإجراء', message = 'هل أنت متأكد؟', okText = 'تأكيد', cancelText = 'إلغاء', onOk = null, isDanger = true, checkboxLabel = null, icon = null }) {
  const modal = document.getElementById('app-confirm-modal');
  if (!modal) {
    if (confirm(message)) onOk?.(false);
    return;
  }

  const style = CONFIRM_STYLES[isDanger ? 'danger' : 'question'];
  const iconBox = document.getElementById('confirm-modal-icon-bg');
  iconBox.className = style.icon;
  iconBox.innerHTML = `<i id="confirm-modal-icon" data-lucide="${icon || (isDanger ? 'trash-2' : 'help-circle')}" class="w-7 h-7"></i>`;
  lucide.createIcons({ root: iconBox });
  document.getElementById('confirm-modal-ok').className = style.ok;

  document.getElementById('confirm-modal-title').innerText = title;
  document.getElementById('confirm-modal-msg').innerText = message;

  const checkWrap = document.getElementById('confirm-modal-check-wrap');
  const check = document.getElementById('confirm-modal-check');
  if (checkWrap && check) {
    check.checked = false;
    document.getElementById('confirm-modal-check-label').innerText = checkboxLabel || '';
    checkWrap.classList.toggle('hidden', !checkboxLabel);
  }

  const okBtn = document.getElementById('confirm-modal-ok');
  const cancelBtn = document.getElementById('confirm-modal-cancel');

  okBtn.innerText = okText;
  cancelBtn.innerText = cancelText;

  confirmCallback = onOk;
  modal.classList.remove('hidden');
}

function closeConfirmModal() {
  const modal = document.getElementById('app-confirm-modal');
  if (modal) modal.classList.add('hidden');
  confirmCallback = null;
}

document.addEventListener('DOMContentLoaded', () => {
  const okBtn = document.getElementById('confirm-modal-ok');
  const cancelBtn = document.getElementById('confirm-modal-cancel');
  if (okBtn) {
    okBtn.addEventListener('click', () => {
      const cb = confirmCallback;
      const checked = !!document.getElementById('confirm-modal-check')?.checked;
      closeConfirmModal();
      if (cb) cb(checked);
    });
  }
  if (cancelBtn) {
    cancelBtn.addEventListener('click', closeConfirmModal);
  }
});

// WebSocket Connection
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[OpenHydro WS] Connected to Telemetry Hub');
    // A link copied while this page was closed or disconnected (e.g. started with Windows)
    loadClipboardOffer();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleWebSocketMessage(msg);
    } catch (e) {
      console.error('[OpenHydro WS] Parse error:', e);
    }
  };

  ws.onclose = () => {
    console.warn('[OpenHydro WS] Disconnected. Reconnecting in 2s...');
    setTimeout(initWebSocket, 2000);
  };
}

function handleWebSocketMessage(msg) {
  const { type, data } = msg;

  if (type === 'snapshot') {
    tasks = {};
    data.forEach(t => { tasks[t.id] = t; });
  } else if (type === 'task_added' || type === 'task_updated' || type === 'task_progress' || type === 'task_completed' || type === 'task_error') {
    tasks[data.id] = data;
    // The copied link was added another way (e.g. captured by the browser extension)
    if (type === 'task_added' && clipboardOffer && clipboardOffer.url === data.url) hideClipboardOffer();
  } else if (type === 'clipboard_link' || type === 'clipboard_open') {
    showClipboardOffer(data);
    return;
  } else if (type.startsWith('component_')) {
    handleComponentEvent(type, data);
    return;
  } else if (type === 'task_cancelled') {
    delete tasks[data.id];
  } else if (type === 'auto_shutdown_triggered') {
    handleShutdownNotification(data?.countdown || 60);
  }

  scheduleRender();
}

// Coalesce bursts of messages into one list rebuild per animation frame
// (frames also pause while the window is hidden in the tray)
let renderScheduled = false;
function scheduleRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    updateGlobalStats();
    renderTasks();
  });
}

// Global Stats & Counters
function updateGlobalStats() {
  let totalSpeed = 0;
  let countAll = 0;
  let countDownloading = 0;
  let countCompleted = 0;
  let countPaused = 0;

  Object.values(tasks).forEach(t => {
    countAll++;
    if (t.status === 'downloading') {
      countDownloading++;
      totalSpeed += t.speed || 0;
    } else if (t.status === 'completed') {
      countCompleted++;
    } else if (t.status === 'paused') {
      countPaused++;
    }
  });

  const speedDisplay = document.getElementById('global-speed-display');
  if (speedDisplay) speedDisplay.innerText = formatSpeed(totalSpeed);

  document.getElementById('count-all').innerText = countAll;
  document.getElementById('count-downloading').innerText = countDownloading;
  document.getElementById('count-completed').innerText = countCompleted;
  document.getElementById('count-paused').innerText = countPaused;
}

// Fetch System Info
async function fetchSystemInfo() {
  try {
    const res = await fetch('/api/system');
    const data = await res.json();
    const diskElem = document.getElementById('disk-free-display');
    if (diskElem) {
      // null: the download folder's drive is not available (e.g. an unplugged external disk)
      diskElem.innerText = data.disk_free_bytes == null
        ? 'المساحة الحرة: غير متاحة'
        : `المساحة الحرة: ${formatBytes(data.disk_free_bytes)}`;
    }
  } catch (e) {
    console.error('Failed to fetch system info:', e);
  }
}

// Render Tasks List
function renderTasks() {
  const container = document.getElementById('tasks-container');
  const searchInput = document.getElementById('search-input');
  currentSearch = searchInput ? searchInput.value.toLowerCase().trim() : '';

  let filtered = Object.values(tasks).filter(task => {
    // Category filter
    if (currentCategory === 'downloading' && task.status !== 'downloading') return false;
    if (currentCategory === 'completed' && task.status !== 'completed') return false;
    if (currentCategory === 'paused' && task.status !== 'paused') return false;
    if (['video', 'audio', 'compressed', 'document', 'program', 'torrent'].includes(currentCategory)) {
      if (task.category !== currentCategory) return false;
    }

    // Search filter
    if (currentSearch && !task.filename.toLowerCase().includes(currentSearch)) {
      return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    renderedRows.clear();
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center h-64 text-gray-500 gap-3">
        <i data-lucide="inbox" class="w-12 h-12 stroke-[1.5]"></i>
        <p class="text-sm">لا توجد تنزيلات متطابقة</p>
      </div>
    `;
    lucide.createIcons({ root: container });
    return;
  }

  // Sort: downloading first, then by created_at desc
  filtered.sort((a, b) => {
    if (a.status === 'downloading' && b.status !== 'downloading') return -1;
    if (b.status === 'downloading' && a.status !== 'downloading') return 1;
    return b.created_at - a.created_at;
  });

  // Rows are keyed by task id and only rebuilt when their HTML changes: a progress tick replaces the
  // downloading rows and leaves the rest alone (no lost hover, no icons re-created page-wide)
  const shown = new Set(filtered.map(task => task.id));
  for (const [id, row] of renderedRows) {
    if (!shown.has(id)) {
      row.el.remove();
      renderedRows.delete(id);
    }
  }
  for (const el of [...container.children]) {
    if (!el.dataset.taskId) el.remove();  // the "no downloads" placeholder
  }

  let previous = null;
  for (const task of filtered) {
    const html = renderTaskCard(task);
    let row = renderedRows.get(task.id);
    if (!row || row.html !== html) {
      const template = document.createElement('template');
      template.innerHTML = html.trim();
      const el = template.content.firstElementChild;
      el.dataset.taskId = task.id;
      lucide.createIcons({ root: el });
      if (row) row.el.replaceWith(el);
      row = { el, html };
      renderedRows.set(task.id, row);
    }
    const expected = previous ? previous.nextElementSibling : container.firstElementChild;
    if (row.el !== expected) container.insertBefore(row.el, expected);
    previous = row.el;
  }
}

function renderTaskCard(task) {
  const isDownloading = task.status === 'downloading';
  const isCompleted = task.status === 'completed';
  const isPaused = task.status === 'paused';
  const isError = task.status === 'error';
  const isQueued = task.status === 'queued';

  let statusBadge = '';
  if (isDownloading) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
        جاري التنزيل (${task.num_connections} مسارات)
      </span>
    `;
  } else if (isCompleted) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
        <i data-lucide="check" class="w-3 h-3"></i>
        مكتمل
      </span>
    `;
  } else if (isPaused) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30">
        متوقف مؤقتاً
      </span>
    `;
  } else if (isError) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">
        خطأ في التحميل
      </span>
    `;
  } else if (isQueued) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-500/10 text-slate-300 border border-slate-500/30" title="سيبدأ تلقائياً عند انتهاء أحد التنزيلات الجارية">
        <i data-lucide="clock" class="w-3 h-3"></i>
        في الانتظار
      </span>
    `;
  }

  const torrentBadge = task.is_torrent ? `
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-orange-500/10 text-orange-400 border border-orange-500/30">
      <i data-lucide="magnet" class="w-3 h-3"></i>
      <span>تورنت (S:${task.seeders || 0} | P:${task.peers || 0})</span>
    </span>
  ` : '';

  // This download's own speed limit (set from its download window)
  const limitBadge = task.speed_limit_kbps > 0 && !isCompleted ? `
    <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-500/10 text-purple-300 border border-purple-500/30" title="حد سرعة التنزيل ده">
      <i data-lucide="gauge" class="w-3 h-3"></i>
      <span dir="ltr">${formatSpeed(task.speed_limit_kbps * 1024)}</span>
    </span>
  ` : '';

  // Segment visualization html
  const chunksHtml = task.chunks && task.chunks.length > 0 ? `
    <div class="mt-3 pt-3 border-t border-white/5">
      <div class="flex flex-wrap items-center justify-between gap-x-2 text-[11px] text-gray-400 mb-1.5">
        <div class="flex items-center gap-1.5">
          <i data-lucide="cpu" class="w-3.5 h-3.5 text-cyan-400"></i>
          <span>حالة الكتل المتزامنة (Dynamic Segmentation):</span>
        </div>
        <span class="font-mono text-cyan-400 font-semibold">${task.chunks.length} كتل نشطة</span>
      </div>
      <div class="segment-container">
        ${task.chunks.map(c => `
          <div class="segment-cell ${c.status}" title="الكتلة #${c.id}: ${c.progress_percent}% (${formatSpeed(c.speed)})">
            <div class="segment-fill" style="width: ${c.progress_percent}%;"></div>
          </div>
        `).join('')}
      </div>
    </div>
  ` : '';

  return `
    <div class="glass-panel glass-panel-hover rounded-2xl p-3 sm:p-4 transition-all ${task.id === selectedTaskId ? 'ring-2 ring-cyan-500/60' : ''}">
      <div class="flex flex-wrap sm:flex-nowrap items-start justify-between gap-3 sm:gap-4">
        <!-- Icon & Title (a row of its own on phones, the buttons go under it) -->
        <div class="flex items-start gap-3 flex-1 basis-full sm:basis-0 min-w-0">
          <div class="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center shrink-0 border border-white/5 text-cyan-400">
            <i data-lucide="${getCategoryIcon(task.category)}" class="w-5 h-5"></i>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <h3 class="font-semibold text-sm text-gray-100 truncate" title="${escapeHtml(task.filename)}">${escapeHtml(task.filename)}</h3>
              ${statusBadge}
              ${torrentBadge}
              ${limitBadge}
            </div>
            <p class="text-xs text-gray-400 truncate mt-0.5 font-mono">${escapeHtml(task.url)}</p>
            ${task.phase ? `<p class="text-[11px] text-amber-300 truncate mt-0.5" title="${escapeHtml(task.phase)}">${escapeHtml(task.phase)}</p>` : ''}
            ${isError && task.error_message ? `<p class="text-[11px] text-rose-400 truncate mt-0.5" title="${escapeHtml(task.error_message)}">${escapeHtml(task.error_message)}</p>` : ''}
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center gap-1.5 shrink-0">
          ${isCompleted ? `
            ${(task.category === 'compressed' || task.filename.endsWith('.zip') || task.filename.endsWith('.tar.gz') || task.filename.endsWith('.tar')) ? `
              <button onclick="extractTaskArchive('${task.id}')" title="استخراج وفك ضغط الأرشيف" class="p-2 rounded-xl glass-panel hover:bg-purple-500/20 text-purple-400 transition-colors">
                <i data-lucide="archive" class="w-4 h-4"></i>
              </button>
            ` : ''}
            <button onclick="openTaskFile('${task.id}')" title="تشغيل / فتح الملف" class="p-2 rounded-xl glass-panel hover:bg-emerald-500/20 text-emerald-400 transition-colors">
              <i data-lucide="play" class="w-4 h-4"></i>
            </button>
            <button onclick="openTaskFolder('${task.id}')" title="فتح المجلد في Explorer" class="p-2 rounded-xl glass-panel hover:bg-cyan-500/20 text-cyan-400 transition-colors">
              <i data-lucide="folder-open" class="w-4 h-4"></i>
            </button>
          ` : ''}

          ${(!isCompleted && (task.category === 'video' || ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.ts'].some(ext => task.filename.toLowerCase().endsWith(ext)))) ? `
            <button onclick="openVideoPreview('${task.id}')" title="معاينة وتشغيل الفيديو أثناء التحميل" class="p-2 rounded-xl glass-panel hover:bg-cyan-500/20 text-cyan-400 transition-colors">
              <i data-lucide="play-circle" class="w-4 h-4"></i>
            </button>
          ` : ''}

          ${(isDownloading || isQueued) ? `
            <button onclick="pauseTask('${task.id}')" title="إيقاف مؤقت" class="p-2 rounded-xl glass-panel hover:bg-white/10 text-amber-400 transition-colors">
              <i data-lucide="pause" class="w-4 h-4"></i>
            </button>
          ` : ''}

          ${(isPaused || isError) ? `
            <button onclick="resumeTask('${task.id}')" title="استئناف" class="p-2 rounded-xl glass-panel hover:bg-white/10 text-emerald-400 transition-colors">
              <i data-lucide="play" class="w-4 h-4"></i>
            </button>
            <button onclick="openRefreshModal('${task.id}')" title="تحديث عنوان التحميل (Refresh Address)" class="p-2 rounded-xl glass-panel hover:bg-white/10 text-amber-400 transition-colors">
              <i data-lucide="refresh-cw" class="w-4 h-4"></i>
            </button>
          ` : ''}

          <button onclick="deleteTask('${task.id}')" title="حذف" class="p-2 rounded-xl glass-panel hover:bg-white/10 text-rose-400 transition-colors">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </div>
      </div>

      <!-- Main Progress Bar -->
      <div class="mt-3">
        <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden relative">
          <div class="h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full transition-all duration-300 ${isDownloading ? 'progress-animated' : ''}" style="width: ${task.progress_percent}%;"></div>
        </div>

        <!-- Metadata footer -->
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-gray-400 mt-2 font-mono">
          <div class="flex items-center gap-3">
            <span dir="ltr" class="text-gray-200 font-semibold">${task.progress_percent}%</span>
            <span dir="ltr">${formatBytes(task.downloaded_bytes)} / ${task.total_bytes ? formatBytes(task.total_bytes) : 'غير محدد'}</span>
          </div>

          <div class="flex items-center gap-4">
            ${isDownloading ? `
              <span class="text-cyan-400 font-bold flex items-center gap-1">
                <i data-lucide="zap" class="w-3.5 h-3.5 fill-cyan-400"></i>
                <span dir="ltr">${formatSpeed(task.speed)}</span>
              </span>
              <span>الوقت المتبقي: ${formatETA(task.eta_seconds)}</span>
            ` : ''}
          </div>
        </div>
      </div>

      <!-- Dynamic Chunks Visualizer -->
      ${chunksHtml}
    </div>
  `;
}

// Category filter click
function setCategoryFilter(cat) {
  currentCategory = cat;
  document.querySelectorAll('.category-btn').forEach(btn => {
    if (btn.dataset.cat === cat) {
      btn.className = 'category-btn w-full flex items-center justify-between px-3 py-2 rounded-xl text-sm font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20';
    } else {
      btn.className = 'category-btn w-full flex items-center justify-between px-3 py-2 rounded-xl text-sm font-medium text-gray-300 hover:bg-white/5';
    }
  });
  // The picker that replaces the sidebar on phones
  const select = document.getElementById('category-select');
  if (select) select.value = cat;
  renderTasks();
}

// Narrow windows and phones: the header's secondary actions open as a panel under it
function toggleHeaderMenu(open) {
  const panel = document.getElementById('header-actions');
  const show = open === undefined ? panel.classList.contains('hidden') : open;
  panel.classList.toggle('hidden', !show);
  panel.classList.toggle('grid', show);
}

document.addEventListener('click', (event) => {
  const panel = document.getElementById('header-actions');
  if (!panel || panel.classList.contains('hidden')) return;
  // Picking an action, or clicking anywhere outside the menu, closes it (the limiter keeps its own submenu)
  const action = event.target.closest('#header-actions button[onclick]');
  const inside = event.target.closest('#header-actions, #header-menu-btn');
  if (action || !inside) toggleHeaderMenu(false);
});

// Task Actions
async function pauseTask(id) {
  try {
    await fetch(`/api/tasks/${id}/pause`, { method: 'POST' });
  } catch (e) {
    console.error(e);
  }
}

async function resumeTask(id) {
  try {
    await fetch(`/api/tasks/${id}/resume`, { method: 'POST' });
  } catch (e) {
    console.error(e);
  }
}

async function deleteTask(id) {
  const finished = tasks[id]?.status === 'completed';
  showConfirm({
    title: 'إزالة التنزيل',
    message: finished
      ? 'سيتم إزالة التنزيل من القائمة، ويبقى الملف كما هو على جهازك.'
      : 'سيتم إيقاف التنزيل وإزالته من القائمة وحذف ملفاته المؤقتة.',
    okText: 'إزالة',
    cancelText: 'إلغاء',
    checkboxLabel: 'حذف الملف من الجهاز أيضاً',
    onOk: async (deleteFile) => {
      try {
        const res = await fetch(`/api/tasks/${id}?delete_file=${deleteFile ? 'true' : 'false'}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        delete tasks[id];
        renderTasks();
        updateGlobalStats();
        showToast(deleteFile ? 'تم حذف التنزيل والملف من الجهاز' : 'تمت إزالة التنزيل من القائمة', 'info');
      } catch (e) {
        showToast('فشل حذف التنزيل: ' + e, 'error');
      }
    }
  });
}

// Bulk actions go through the server queue so "resume all" honours the concurrent download limit
async function pauseAllTasks() {
  try {
    await fetch('/api/queue/pause_all', { method: 'POST' });
  } catch (e) {
    console.error(e);
  }
}

async function resumeAllTasks() {
  try {
    await fetch('/api/queue/resume_all', { method: 'POST' });
  } catch (e) {
    console.error(e);
  }
}

function clearCompletedTasks() {
  const count = Object.values(tasks).filter(t => t.status === 'completed').length;
  if (!count) {
    showToast('مفيش تنزيلات مكتملة في القائمة', 'info');
    return;
  }
  showConfirm({
    title: 'مسح المكتملة من القائمة',
    message: `هيتشال ${count} تنزيل مكتمل من القائمة. الملفات نفسها هتفضل في مكانها.`,
    okText: 'مسح من القائمة',
    isDanger: false,
    icon: 'list-x',
    onOk: async () => {
      try {
        const res = await fetch('/api/queue/clear_completed', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
        showToast(`تم مسح ${data.removed} من القائمة`, 'success');
      } catch (e) {
        showToast('تعذر مسح المكتملة: ' + e.message, 'error');
      }
    },
  });
}

async function openTaskFile(id) {
  try {
    const res = await fetch(`/api/tasks/${id}/open_file`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast('🚀 جاري تشغيل / فتح الملف...', 'info');
      return;
    }
    showToast('تعذر فتح الملف: ' + (data.detail || data.message || 'الملف غير موجود'), 'warning');
  } catch (e) {
    showToast('حدث خطأ أثناء فتح الملف: ' + e, 'error');
  }
}

async function openTaskFolder(id) {
  try {
    const res = await fetch(`/api/tasks/${id}/open_folder`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast('📂 تم فتح مجلد التنزيل في مستكشف الملفات!', 'success');
      return;
    }
    showToast('تعذر فتح المجلد: ' + (data.detail || data.message || 'المجلد غير موجود'), 'warning');
  } catch (e) {
    showToast('حدث خطأ أثناء فتح المجلد: ' + e, 'error');
  }
}

async function extractTaskArchive(id) {
  try {
    showToast('📦 جاري فك ضغط الأرشيف...', 'info');
    const res = await fetch(`/api/tasks/${id}/extract`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast(`✅ تم فك الضغط بنجاح في مجلد: ${data.extracted_to}`, 'success');
      return;
    }
    showToast('تعذر فك الضغط: ' + (data.detail || data.message || 'صيغة غير مدعومة'), 'warning');
  } catch (e) {
    showToast('حدث خطأ أثناء فك الضغط: ' + e, 'error');
  }
}

// --- Video Preview Controller (Instant Stream while downloading) ---
let currentPreviewTaskId = null;

function openVideoPreview(taskId) {
  const task = tasks[taskId];
  if (!task) return;
  currentPreviewTaskId = taskId;

  const modal = document.getElementById('preview-modal');
  const filenameEl = document.getElementById('preview-filename');
  const player = document.getElementById('preview-player');
  const badgeEl = document.getElementById('preview-status-badge');

  if (filenameEl) filenameEl.innerText = task.filename;
  if (badgeEl) {
    if (task.status === 'completed') {
      badgeEl.innerText = 'مكتمل - تشغيل كامل';
      badgeEl.className = 'px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 text-[10px] font-mono';
    } else {
      badgeEl.innerText = `معاينة فورية أثناء التحميل (${task.progress_percent}%)`;
      badgeEl.className = 'px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 text-[10px] font-mono';
    }
  }

  if (player) {
    player.src = `/api/tasks/${taskId}/preview_stream?t=${Date.now()}`;
    player.load();
    player.play().catch(() => {});
  }

  modal.classList.remove('hidden');
  lucide.createIcons();
}

function closePreviewModal() {
  const modal = document.getElementById('preview-modal');
  const player = document.getElementById('preview-player');
  if (player) {
    player.pause();
    player.src = '';
  }
  currentPreviewTaskId = null;
  modal.classList.add('hidden');
}

async function openExternalPreview() {
  if (!currentPreviewTaskId) return;
  try {
    const res = await fetch(`/api/tasks/${currentPreviewTaskId}/preview_external`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast('🚀 تم فتح المعاينة في المشغل الخارجي بنجاح!', 'success');
      return;
    }
    showToast('تعذر فتح المشغل الخارجي: ' + (data.detail || data.message || 'خطأ'), 'warning');
  } catch (e) {
    showToast('حدث خطأ: ' + e, 'error');
  }
}

// --- Auto-Shutdown Controller ---
let shutdownCountdownInterval = null;

function handleShutdownNotification(countdownSec = 60) {
  const banner = document.getElementById('shutdown-banner');
  const timerEl = document.getElementById('shutdown-timer');
  if (!banner || !timerEl) return;

  banner.classList.remove('hidden');
  let remaining = countdownSec;
  timerEl.innerText = remaining;

  if (shutdownCountdownInterval) clearInterval(shutdownCountdownInterval);
  shutdownCountdownInterval = setInterval(() => {
    remaining -= 1;
    timerEl.innerText = remaining;
    if (remaining <= 0) {
      clearInterval(shutdownCountdownInterval);
      shutdownCountdownInterval = null;
    }
  }, 1000);
}

async function cancelShutdown() {
  if (shutdownCountdownInterval) {
    clearInterval(shutdownCountdownInterval);
    shutdownCountdownInterval = null;
  }
  document.getElementById('shutdown-banner')?.classList.add('hidden');
  try {
    await fetch('/api/shutdown/cancel', { method: 'POST' });
    showToast('🛑 تم إلغاء إيقاف تشغيل الكمبيوتر بنجاح!', 'info');
  } catch (e) {
    console.error(e);
  }
}

// Speed Limiter
async function setSpeedLimit(kbps) {
  try {
    await fetch('/api/settings/limiter', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_kbps: kbps })
    });
    const limiterText = document.getElementById('limiter-status-text');
    if (limiterText) {
      limiterText.innerText = kbps === 0 ? 'بدون تحديد' : `${kbps} KB/s`;
    }
  } catch (e) {
    console.error(e);
  }
}

// Add Modal Logic
function openAddModal(prefillUrl = '') {
  const modal = document.getElementById('add-modal');
  modal.classList.remove('hidden');
  if (appSettings) {
    document.getElementById('modal-connections-select').value = String(appSettings.default_connections);
  }
  const input = document.getElementById('modal-url-input');
  if (prefillUrl) {
    input.value = prefillUrl;
    onUrlInputDebounced();
  } else {
    input.focus();
  }
}

function closeAddModal() {
  document.getElementById('add-modal').classList.add('hidden');
  document.getElementById('modal-url-input').value = '';
  document.getElementById('modal-filename-input').value = '';
  document.getElementById('probe-info-card').classList.add('hidden');
  document.getElementById('video-quality-container').classList.add('hidden');
  const plBanner = document.getElementById('modal-playlist-banner');
  if (plBanner) plBanner.classList.add('hidden');
  const thumb = document.getElementById('probe-thumbnail');
  if (thumb) {
    thumb.src = '';
    thumb.classList.add('hidden');
  }
}

function switchToPlaylistModal() {
  const url = document.getElementById('modal-url-input').value.trim();
  closeAddModal();
  openPlaylistModal(url);
}

function onUrlInputDebounced() {
  clearTimeout(probeTimeout);
  probeTimeout = setTimeout(probeModalUrl, 500);
}

async function probeModalUrl() {
  const url = document.getElementById('modal-url-input').value.trim();
  if (!url || !url.startsWith('http')) return;

  const spinner = document.getElementById('modal-probe-spinner');
  const infoCard = document.getElementById('probe-info-card');
  spinner.classList.remove('hidden');

  try {
    const res = await fetch(`/api/probe?url=${encodeURIComponent(url)}`);
    const data = await res.json();

    const thumbElem = document.getElementById('probe-thumbnail');
    const qualityContainer = document.getElementById('video-quality-container');
    const qualitySelect = document.getElementById('modal-quality-select');
    const plBanner = document.getElementById('modal-playlist-banner');

    if (data.is_playlist) {
      if (plBanner) plBanner.classList.remove('hidden');
    } else {
      if (plBanner) plBanner.classList.add('hidden');
    }

    if (data.is_video_platform) {
      document.getElementById('probe-filename').innerText = data.title || data.filename;
      document.getElementById('probe-size').innerText = data.total_bytes ? formatBytes(data.total_bytes) : 'فيديو منصة (حجم حسب الجودة)';
      document.getElementById('probe-ranges').innerText = '⚡ مدعوم (مسارات متعددة متوازية - Multi-Threaded Turbo 🚀)';
      document.getElementById('probe-ranges').className = 'text-cyan-400 font-semibold';

      if (data.thumbnail) {
        thumbElem.src = data.thumbnail;
        thumbElem.classList.remove('hidden');
      } else {
        thumbElem.classList.add('hidden');
      }

      // Populate video qualities, audio, and subtitles/transcripts
      if (data.qualities && data.qualities.length > 0) {
        const videoOptions = data.qualities.filter(q => q.type === 'video');
        const audioOptions = data.qualities.filter(q => q.type === 'audio');
        const textOptions = data.qualities.filter(q => q.type === 'subtitle' || q.type === 'text');

        let html = '';
        if (videoOptions.length > 0) {
          html += `<optgroup label="🎬 جودات الفيديو (Video Qualities)">` +
            videoOptions.map(q => `<option value="${q.format_id}" data-ext="${q.ext || 'mp4'}">${q.label}</option>`).join('') +
            `</optgroup>`;
        }
        if (audioOptions.length > 0) {
          html += `<optgroup label="🎵 استخراج الصوت (Audio Only)">` +
            audioOptions.map(q => `<option value="${q.format_id}" data-ext="${q.ext || 'mp3'}">${q.label}</option>`).join('') +
            `</optgroup>`;
        }
        if (textOptions.length > 0) {
          html += `<optgroup label="📝 الترجمة والنصوص المفرغة (Subtitles & Text)">` +
            textOptions.map(q => `<option value="${q.format_id}" data-ext="${q.ext || 'srt'}">${q.label}</option>`).join('') +
            `</optgroup>`;
        }
        qualitySelect.innerHTML = html;
        qualityContainer.classList.remove('hidden');
      } else {
        qualityContainer.classList.add('hidden');
      }
    } else if (data.is_hls) {
      thumbElem.classList.add('hidden');
      qualityContainer.classList.add('hidden');
      document.getElementById('probe-filename').innerText = data.filename;
      document.getElementById('probe-size').innerText = 'بث مجزأ HLS Stream (.m3u8)';
      let qualText = 'نعم (HLS Stream)';
      if (data.hls_info && data.hls_info.qualities) {
        const resList = data.hls_info.qualities.map(q => q.resolution).filter(r => r && r !== 'Auto');
        if (resList.length > 0) qualText = `دقات متعددة: ${resList.join(', ')}`;
      }
      document.getElementById('probe-ranges').innerText = qualText;
      document.getElementById('probe-ranges').className = 'text-cyan-400 font-semibold';
    } else {
      thumbElem.classList.add('hidden');
      qualityContainer.classList.add('hidden');
      document.getElementById('probe-filename').innerText = data.filename;
      document.getElementById('probe-size').innerText = data.total_bytes ? formatBytes(data.total_bytes) : 'غير معروف';
      document.getElementById('probe-ranges').innerText = data.supports_ranges ? 'مدعوم (Ranges Supported)' : 'غير مدعوم (مسار أحادي)';
      document.getElementById('probe-ranges').className = data.supports_ranges ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold';
    }

    if (!document.getElementById('modal-filename-input').value) {
      document.getElementById('modal-filename-input').value = data.filename;
    }
    infoCard.classList.remove('hidden');
  } catch (e) {
    console.error(e);
  } finally {
    spinner.classList.add('hidden');
  }
}

function onQualityChanged() {
  const select = document.getElementById('modal-quality-select');
  const filenameInput = document.getElementById('modal-filename-input');
  if (!select || !filenameInput) return;

  const val = select.value;
  let cur = filenameInput.value.trim();
  if (!cur) return;

  const dot = cur.lastIndexOf('.');
  const base = dot !== -1 ? cur.substring(0, dot) : cur;

  if (val === 'audio_mp3') {
    filenameInput.value = base + '.mp3';
  } else if (val === 'audio_m4a') {
    filenameInput.value = base + '.m4a';
  } else if (val === 'subtitle_srt') {
    filenameInput.value = base + '.srt';
  } else if (val === 'subtitle_txt') {
    filenameInput.value = base + '.txt';
  } else {
    filenameInput.value = base + '.mp4';
  }
}

const STATUS_NAMES = {
  completed: 'مكتمل', downloading: 'جاري تنزيله', queued: 'في الانتظار', paused: 'متوقف مؤقتاً', error: 'فيه خطأ',
};

async function submitNewTask(downloadAgain = false) {
  const url = document.getElementById('modal-url-input').value.trim();
  const filename = document.getElementById('modal-filename-input').value.trim();
  const connections = parseInt(document.getElementById('modal-connections-select').value, 10);

  const qualityContainer = document.getElementById('video-quality-container');
  const qualitySelect = document.getElementById('modal-quality-select');
  const formatId = (!qualityContainer.classList.contains('hidden') && qualitySelect) ? qualitySelect.value : null;

  if (!url) {
    showToast('يرجى إدخال رابط صالح', 'warning');
    return;
  }

  // The same link is already in the list (IDM asks too): a second copy only on purpose
  const existing = !downloadAgain && Object.values(tasks).find(t => t.url === url);
  if (existing) {
    showConfirm({
      title: 'الرابط ده موجود في القائمة',
      message: `"${existing.filename}" (${STATUS_NAMES[existing.status] || existing.status}). تنزّله مرة تانية في ملف جديد؟`,
      okText: 'تنزيل مرة تانية',
      cancelText: 'لا',
      isDanger: false,
      icon: 'copy',
      onOk: () => submitNewTask(true),
    });
    return;
  }

  try {
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        filename: filename || null,
        num_connections: connections,
        auto_start: true,
        format_id: formatId
      })
    });
    const data = await res.json();
    tasks[data.id] = data;
    closeAddModal();
    renderTasks();
    updateGlobalStats();
    showToast(`⚡ تم بدء تنزيل [${data.filename}] بنجاح!`, 'success');
  } catch (e) {
    showToast('حدث خطأ أثناء إضافة التنزيل: ' + e, 'error');
  }
}

// Refresh URL Modal Logic (IDM Smart Address Refresh)
function openRefreshModal(taskId) {
  const modal = document.getElementById('refresh-modal');
  modal.classList.remove('hidden');
  document.getElementById('refresh-task-id').value = taskId;
  const task = tasks[taskId];
  const input = document.getElementById('refresh-url-input');
  input.value = task ? task.url : '';
  input.focus();
}

function closeRefreshModal() {
  document.getElementById('refresh-modal').classList.add('hidden');
  document.getElementById('refresh-task-id').value = '';
  document.getElementById('refresh-url-input').value = '';
}

async function submitRefreshUrl() {
  const taskId = document.getElementById('refresh-task-id').value;
  const newUrl = document.getElementById('refresh-url-input').value.trim();

  if (!newUrl || (!newUrl.startsWith('http://') && !newUrl.startsWith('https://'))) {
    showToast('يرجى إدخال رابط صالح يبدأ بـ http:// أو https://', 'warning');
    return;
  }

  try {
    const res = await fetch(`/api/tasks/${taskId}/refresh_url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: newUrl })
    });
    if (res.ok) {
      if (tasks[taskId]) tasks[taskId].url = newUrl;
      closeRefreshModal();
      renderTasks();
      showToast('⚡ تم تحديث رابط التنزيل بنجاح!', 'success');
    } else {
      showToast('فشل تحديث الرابط', 'error');
    }
  } catch (e) {
    showToast('حدث خطأ: ' + e, 'error');
  }
}

// Smart Scheduler Modal Logic
async function openSchedulerModal() {
  const modal = document.getElementById('scheduler-modal');
  modal.classList.remove('hidden');
  await loadSchedulerSettings();
}

function closeSchedulerModal() {
  document.getElementById('scheduler-modal').classList.add('hidden');
}

async function loadSchedulerSettings() {
  try {
    const res = await fetch('/api/settings/scheduler');
    const data = await res.json();
    document.getElementById('sched-max-active').value = data.max_active || 3;
    document.getElementById('sched-time').value = data.scheduled_time || '';
    document.getElementById('sched-shutdown').checked = !!data.auto_shutdown;
  } catch (e) {
    console.error('Failed to load scheduler settings:', e);
  }
}

async function saveSchedulerSettings() {
  const maxActive = parseInt(document.getElementById('sched-max-active').value, 10);
  const timeVal = document.getElementById('sched-time').value.trim() || null;
  const autoShutdown = document.getElementById('sched-shutdown').checked;

  try {
    const res = await fetch('/api/settings/scheduler', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        max_active: maxActive,
        scheduled_time: timeVal,
        auto_shutdown: autoShutdown
      })
    });
    if (res.ok) {
      closeSchedulerModal();
      showToast('⚡ تم حفظ إعدادات الجدولة والطوابير بنجاح!', 'success');
    }
  } catch (e) {
    showToast('حدث خطأ أثناء حفظ الجدولة: ' + e, 'error');
  }
}

// Drag & Drop Setup
const dropZone = document.getElementById('drop-zone');
if (dropZone) {
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('border-cyan-400', 'bg-cyan-900/40');
  });

  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('border-cyan-400', 'bg-cyan-900/40');
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('border-cyan-400', 'bg-cyan-900/40');
    const text = e.dataTransfer.getData('text');
    if (text && text.startsWith('http')) {
      openAddModal(text);
    }
  });
}

// --- Settings ---
let appSettings = null;

async function loadSettings() {
  const res = await fetch('/api/settings');
  if (!res.ok) throw new Error('HTTP ' + res.status);
  appSettings = await res.json();
  return appSettings;
}

async function openSettingsModal() {
  document.getElementById('settings-modal').classList.remove('hidden');
  lucide.createIcons();
  loadFfmpegStatus();
  try {
    const s = await loadSettings();
    const dirInput = document.getElementById('set-download-dir');
    dirInput.value = s.download_dir || '';
    dirInput.placeholder = s.effective_download_dir;
    document.getElementById('set-default-connections').value = String(s.default_connections);
    document.getElementById('set-auto-resume').checked = s.auto_resume;
    document.getElementById('set-notify').checked = s.notify_on_complete;
    document.getElementById('set-download-window').checked = s.show_download_window;
    document.getElementById('set-clipboard').checked = s.clipboard_monitor;
    document.getElementById('set-remote').checked = s.remote_access;
    // null: only the installed app can register itself to start with Windows
    const autostart = document.getElementById('set-autostart');
    autostart.checked = !!s.start_with_windows;
    autostart.disabled = s.start_with_windows === null;
    document.getElementById('set-autostart-note').classList.toggle('hidden', s.start_with_windows !== null);
  } catch (e) {
    showToast('تعذر تحميل الإعدادات: ' + e.message, 'error');
  }
}

function closeSettingsModal() {
  document.getElementById('settings-modal').classList.add('hidden');
  if (window.location.hash === '#settings') history.replaceState(null, '', window.location.pathname);
}

async function saveSettings() {
  const autostart = document.getElementById('set-autostart');
  const changes = {
    download_dir: document.getElementById('set-download-dir').value.trim() || null,
    default_connections: parseInt(document.getElementById('set-default-connections').value, 10),
    auto_resume: document.getElementById('set-auto-resume').checked,
    notify_on_complete: document.getElementById('set-notify').checked,
    show_download_window: document.getElementById('set-download-window').checked,
    clipboard_monitor: document.getElementById('set-clipboard').checked,
    remote_access: document.getElementById('set-remote').checked,
  };
  if (!autostart.disabled) changes.start_with_windows = autostart.checked;

  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    appSettings = data;
    closeSettingsModal();
    if (data.restart_required) {
      showToast('تم الحفظ. أعد تشغيل البرنامج لتفعيل التحكم من الموبايل', 'warning', 7000);
    } else {
      showToast('تم حفظ الإعدادات', 'success');
    }
  } catch (e) {
    showToast('لم يتم الحفظ: ' + e.message, 'error', 6000);
  }
}

// --- ffmpeg (merging video with audio, MP3): status and manual download in the settings ---
function renderFfmpegStatus(s) {
  const text = document.getElementById('set-ffmpeg-status');
  const button = document.getElementById('set-ffmpeg-install');
  if (!text || !s) return;
  let message;
  let color = 'text-gray-400';
  if (s.installing) {
    const p = s.progress || {};
    message = p.total ? `جاري التنزيل: ${Math.floor(p.done * 100 / p.total)}% من ${formatBytes(p.total)}` : 'جاري التنزيل...';
    color = 'text-cyan-400';
  } else if (s.available) {
    message = s.source === 'openhydro' ? `✓ جاهز (نزّله OpenHydro${s.version ? '، ' + s.version : ''})` : '✓ جاهز (مثبت على الجهاز)';
    color = 'text-emerald-400';
  } else if (s.supported) {
    message = 'غير موجود: هيتنزل تلقائياً أول مرة تنزّل فيديو بجودة عالية أو MP3.';
  } else {
    message = 'غير موجود: ثبّته من مدير الحزم في نظامك (مثلاً apt install ffmpeg).';
    color = 'text-amber-400';
  }
  text.textContent = s.error ? `تعذر التنزيل: ${s.error}` : message;
  text.className = `text-[10px] mt-0.5 ${s.error ? 'text-rose-400' : color}`;
  button.classList.toggle('hidden', !s.supported || s.available || s.installing);
}

async function loadFfmpegStatus() {
  try {
    renderFfmpegStatus(await (await fetch('/api/components/ffmpeg')).json());
  } catch (e) {
    // Not shown: the settings work without it
  }
}

// Counts progress/ready/failed events: the answer to the install request can arrive after them
let ffmpegEvents = 0;

async function installFfmpeg() {
  const eventsBefore = ffmpegEvents;
  try {
    const res = await fetch('/api/components/ffmpeg/install', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    // A newer state already came over the WebSocket ("ready"): do not show "installing" over it
    if (ffmpegEvents === eventsBefore) renderFfmpegStatus(data);
  } catch (e) {
    showToast('تعذر بدء تنزيل ffmpeg: ' + e.message, 'error');
  }
}

function handleComponentEvent(type, data) {
  if (!data || data.name !== 'ffmpeg') return;
  ffmpegEvents++;
  if (type === 'component_progress') {
    renderFfmpegStatus({ supported: true, installing: true, progress: data });
  } else if (type === 'component_ready') {
    renderFfmpegStatus(data);
  } else {
    renderFfmpegStatus({ supported: true, available: false, error: data.error });
  }
}

// --- Keyboard shortcuts (IDM style); the list is shown in the settings window ---

// Topmost first: the confirmation can sit on top of another dialog
const DIALOGS = [
  ['app-confirm-modal', closeConfirmModal], ['preview-modal', closePreviewModal], ['add-modal', closeAddModal],
  ['refresh-modal', closeRefreshModal], ['scheduler-modal', closeSchedulerModal], ['settings-modal', closeSettingsModal],
  ['playlist-modal', closePlaylistModal], ['extension-modal', closeExtensionModal], ['update-modal', closeUpdateModal],
  ['mobile-modal', closeMobileRemoteModal],
];

function openDialog() {
  return DIALOGS.find(([id]) => {
    const el = document.getElementById(id);
    return el && !el.classList.contains('hidden');
  });
}

// Keys and pastes in a text field are for the field, not shortcuts (the target can be the document itself)
function inTextField(target) {
  return target instanceof Element && !!target.closest('input, textarea, select, [contenteditable="true"]');
}

function shownTaskIds() {
  return [...document.querySelectorAll('#tasks-container > [data-task-id]')].map(el => el.dataset.taskId);
}

function selectTask(id) {
  selectedTaskId = id;
  renderTasks();
  document.querySelector(`#tasks-container > [data-task-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
}

document.addEventListener('keydown', (event) => {
  const dialog = openDialog();
  if (event.key === 'Escape') {
    if (dialog) {
      event.preventDefault();
      dialog[1]();
    } else {
      toggleHeaderMenu(false);
    }
    return;
  }
  if (dialog) return;  // the dialog has the keyboard (typing a link, a name...)

  const ctrl = event.ctrlKey || event.metaKey;
  if (ctrl && !event.shiftKey && !event.altKey) {
    const key = event.key.toLowerCase();
    if (key === 'n') {
      event.preventDefault();
      openAddModal();
    } else if (key === 'f') {
      event.preventDefault();
      document.getElementById('search-input').focus();
    }
    return;
  }
  if (event.altKey || inTextField(event.target)) return;

  const ids = shownTaskIds();
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (!ids.length) return;
    event.preventDefault();
    const at = ids.indexOf(selectedTaskId);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    selectTask(ids[at < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, at + step))]);
    return;
  }

  const task = ids.includes(selectedTaskId) ? tasks[selectedTaskId] : null;
  if (!task) return;
  if (event.key === 'Delete') {
    event.preventDefault();
    deleteTask(task.id);
  } else if (event.key === ' ') {
    event.preventDefault();
    if (task.status === 'downloading' || task.status === 'queued') pauseTask(task.id);
    else if (task.status === 'paused' || task.status === 'error') resumeTask(task.id);
  } else if (event.key === 'Enter' && task.status === 'completed') {
    event.preventDefault();
    openTaskFile(task.id);
  }
});

// Ctrl+V outside a text field: a pasted link starts a new download, as pasting into IDM does
document.addEventListener('paste', (event) => {
  if (openDialog() || inTextField(event.target)) return;
  const text = (event.clipboardData?.getData('text') || '').trim();
  if (/^(https?:\/\/|magnet:\?)\S+$/i.test(text)) {
    event.preventDefault();
    openAddModal(text);
  }
});

// A click on a download (not on its buttons) selects it for the keyboard
document.addEventListener('click', (event) => {
  if (!(event.target instanceof Element) || event.target.closest('button, a, input, select')) return;
  const row = event.target.closest('#tasks-container > [data-task-id]');
  if (row) selectTask(row.dataset.taskId);
});

// --- Clipboard monitoring: a download link copied anywhere, waiting for the user ---
// This PC only: the phone remote shares this dashboard but not the PC's clipboard
const IS_LOCAL_DASHBOARD = ['127.0.0.1', 'localhost'].includes(window.location.hostname);
let clipboardOffer = null;

function showClipboardOffer(offer) {
  if (!IS_LOCAL_DASHBOARD || !offer) return;
  // open: the user clicked the Windows notification, so go straight to the add dialog
  if (offer.open) {
    acceptClipboardOffer(offer);
    return;
  }
  clipboardOffer = offer;
  document.getElementById('clipboard-offer-name').textContent = offer.filename || offer.url;
  document.getElementById('clipboard-offer').classList.remove('hidden');
  lucide.createIcons();
}

function hideClipboardOffer() {
  clipboardOffer = null;
  document.getElementById('clipboard-offer').classList.add('hidden');
}

function dismissClipboardOffer() {
  hideClipboardOffer();
  fetch('/api/clipboard/dismiss', { method: 'POST' }).catch(() => {});
}

function acceptClipboardOffer(offer = clipboardOffer) {
  if (!offer) return;
  dismissClipboardOffer();
  openAddModal(offer.url);
}

async function loadClipboardOffer() {
  if (!IS_LOCAL_DASHBOARD) return;
  try {
    const { link } = await (await fetch('/api/clipboard')).json();
    if (link) showClipboardOffer(link);
  } catch (e) {
    // Older server or not reachable yet: the next WebSocket event brings it
  }
}

// Initialization
window.addEventListener('DOMContentLoaded', () => {
  initWebSocket();
  fetchSystemInfo();
  setInterval(fetchSystemInfo, 10000);
  loadSettings().catch(() => {});
  if (window.location.hash === '#settings') openSettingsModal();

  // Check URL params for auto-opening playlist modal
  const urlParams = new URLSearchParams(window.location.search);
  const playlistParam = urlParams.get('playlist');
  if (playlistParam) {
    openPlaylistModal(playlistParam);
  }

  // Silent background check for application updates
  setTimeout(() => checkAppUpdates(false), 2500);
});

// --- Playlist Downloader (Snaptube / Vidmate Style) ---
let currentPlaylistData = null;

function openPlaylistModal(presetUrl = '') {
  const modal = document.getElementById('playlist-modal');
  modal.classList.remove('hidden');
  const input = document.getElementById('playlist-url-input');
  if (presetUrl) {
    input.value = presetUrl;
    probePlaylistUrl();
  }
  lucide.createIcons();
}

function closePlaylistModal() {
  const modal = document.getElementById('playlist-modal');
  modal.classList.add('hidden');
  currentPlaylistData = null;
  document.getElementById('playlist-info-card').classList.add('hidden');
  document.getElementById('playlist-items-container').classList.add('hidden');
  document.getElementById('playlist-submit-btn').disabled = true;
}

async function probePlaylistUrl() {
  const url = document.getElementById('playlist-url-input').value.trim();
  if (!url) {
    showToast('يرجى إدخال رابط قائمة تشغيل أو مجلد Google Drive أو عدة روابط', 'warning');
    return;
  }

  const spinner = document.getElementById('playlist-probe-spinner');
  const infoCard = document.getElementById('playlist-info-card');
  const itemsContainer = document.getElementById('playlist-items-container');
  const submitBtn = document.getElementById('playlist-submit-btn');

  spinner.classList.remove('hidden');
  infoCard.classList.add('hidden');
  itemsContainer.classList.add('hidden');
  submitBtn.disabled = true;

  try {
    const res = await fetch('/api/playlist/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'فشل فحص قائمة التشغيل أو مجلد Google Drive');
    }

    const data = await res.json();
    currentPlaylistData = data;

    document.getElementById('playlist-title-display').innerText = data.title;
    document.getElementById('playlist-meta-display').innerText = `${data.uploader ? data.uploader + ' • ' : ''}إجمالي العناصر: ${data.count}`;
    document.getElementById('playlist-total-count').innerText = data.count;
    document.getElementById('playlist-selected-count').innerText = data.count;

    // Adjust quality options for Google Drive vs YouTube
    const qualitySelect = document.getElementById('playlist-quality-select');
    if (data.is_gdrive) {
      qualitySelect.innerHTML = `
        <optgroup label="⚡ تحميل الملفات الأصلية">
          <option value="original" selected>⚡ الجودة والملف الأصلي بالكامل (Direct Stream / Turbo 32x)</option>
        </optgroup>
        <optgroup label="🎵 استخراج الصوت (إذا كانت فيديوهات)">
          <option value="audio_mp3">تحميل كملف صوتي MP3 (320 kbps)</option>
          <option value="audio_m4a">تحميل كملف صوتي أصلي M4A</option>
        </optgroup>
      `;
    } else {
      qualitySelect.innerHTML = `
        <optgroup label="🎬 جودات الفيديو الموحدة">
          <option value="bestvideo[height<=1080]+bestaudio/best">1080p FHD (عالي الدقة)</option>
          <option value="bestvideo[height<=720]+bestaudio/best" selected>720p HD (موصى به - متوازن)</option>
          <option value="bestvideo[height<=480]+bestaudio/best">480p SD (توفير مساحة)</option>
          <option value="bestvideo[height<=360]+bestaudio/best">360p (دقة منخفضة)</option>
        </optgroup>
        <optgroup label="🎵 استخراج الصوت (Audio Only)">
          <option value="audio_mp3">تحميل كألبوم صوتي MP3 (320 kbps)</option>
          <option value="audio_m4a">تحميل كألبوم صوتي أصلي M4A</option>
        </optgroup>
      `;
    }

    renderPlaylistItems(data.entries);

    infoCard.classList.remove('hidden');
    itemsContainer.classList.remove('hidden');
    submitBtn.disabled = false;
    updatePlaylistSubmitButtonText();
    lucide.createIcons();
    showToast(`⚡ تم فحص المحتوى بنجاح! تم العثور على ${data.count} عنصر.`, 'success');
  } catch (e) {
    showToast(`خطأ أثناء فحص الرابط: ${e.message}`, 'error');
  } finally {
    spinner.classList.add('hidden');
  }
}

function renderPlaylistItems(entries) {
  const container = document.getElementById('playlist-items-container');
  container.innerHTML = entries.map(item => `
    <div class="flex items-center gap-3 p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-all border border-white/5">
      <input type="checkbox" class="playlist-item-checkbox rounded bg-slate-900 border-white/20 text-purple-600 focus:ring-0 cursor-pointer" 
             data-index="${Number(item.index)}" data-url="${escapeHtml(item.url)}" data-download-url="${escapeHtml(item.download_url || item.url)}" data-is-gdrive="${item.is_gdrive ? 'true' : 'false'}" data-title="${encodeURIComponent(item.title)}" onchange="updatePlaylistSelectedCount()" checked>
      <span class="text-xs font-mono text-purple-400 w-6 shrink-0">${Number(item.index)}.</span>
      ${item.thumbnail ? `<img src="${escapeHtml(item.thumbnail)}" class="w-14 h-9 object-cover rounded border border-white/10 shrink-0" alt="">` : ''}
      <div class="flex-1 min-w-0">
        <p class="text-xs text-gray-200 font-medium truncate">${escapeHtml(item.title)}</p>
        ${item.duration ? `<span class="text-[10px] text-gray-500 font-mono">${Math.floor(item.duration / 60)}:${(item.duration % 60).toString().padStart(2, '0')}</span>` : ''}
      </div>
    </div>
  `).join('');
}

function toggleSelectAllPlaylist(checked) {
  const checkboxes = document.querySelectorAll('.playlist-item-checkbox');
  checkboxes.forEach(cb => cb.checked = checked);
  updatePlaylistSelectedCount();
}

function updatePlaylistSelectedCount() {
  const checkboxes = document.querySelectorAll('.playlist-item-checkbox');
  const checked = document.querySelectorAll('.playlist-item-checkbox:checked');
  document.getElementById('playlist-selected-count').innerText = checked.length;
  document.getElementById('playlist-select-all').checked = (checkboxes.length > 0 && checked.length === checkboxes.length);

  const submitBtn = document.getElementById('playlist-submit-btn');
  submitBtn.disabled = (checked.length === 0);
  updatePlaylistSubmitButtonText();
}

function updatePlaylistSubmitButtonText() {
  const checked = document.querySelectorAll('.playlist-item-checkbox:checked');
  const btnText = document.getElementById('playlist-submit-btn-text');
  if (btnText) {
    btnText.innerText = `تحميل العناصر المحددة (${checked.length} عنصر)`;
  }
}

async function submitPlaylistDownload() {
  if (!currentPlaylistData) return;

  const checkboxes = document.querySelectorAll('.playlist-item-checkbox:checked');
  if (checkboxes.length === 0) {
    showToast('يرجى تحديد عنصر واحد على الأقل للتحميل', 'warning');
    return;
  }

  const items = Array.from(checkboxes).map(cb => ({
    url: cb.dataset.url,
    download_url: cb.dataset.downloadUrl || cb.dataset.url,
    is_gdrive: cb.dataset.isGdrive === 'true',
    title: decodeURIComponent(cb.dataset.title),
    index: parseInt(cb.dataset.index, 10)
  }));

  const formatId = document.getElementById('playlist-quality-select').value;
  const submitBtn = document.getElementById('playlist-submit-btn');
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>جاري جدولة العناصر...</span>`;
  lucide.createIcons();

  try {
    const res = await fetch('/api/playlist/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        playlist_title: currentPlaylistData.title,
        format_id: formatId,
        items: items,
        num_connections: 8
      })
    });

    const data = await res.json();
    if (data.status === 'success') {
      closePlaylistModal();
      await fetchTasks();
      showToast(`⚡ تم إدراج ${data.enqueued_count} عنصر بنجاح في طابور التحميل الذكي!`, 'success', 5000);
    } else {
      showToast('حدث خطأ أثناء جدولة قائمة التشغيل أو مجلد Google Drive', 'error');
    }
  } catch (e) {
    showToast(`فشل التحميل: ${e.message}`, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<i data-lucide="download" class="w-4 h-4"></i><span id="playlist-submit-btn-text">تحميل العناصر المحددة</span>`;
    lucide.createIcons();
  }
}



// --- Browser Extension Guide Modal Controller ---
let cachedExtensionPath = '';

async function openExtensionModal() {
  const modal = document.getElementById('extension-modal');
  modal.classList.remove('hidden');
  lucide.createIcons();

  const pathDisplay = document.getElementById('extension-folder-path');
  try {
    const res = await fetch('/api/extension/info');
    if (res.ok) {
      const data = await res.json();
      if (data && data.path) {
        cachedExtensionPath = data.path;
        pathDisplay.innerText = data.path;
        return;
      }
    }
  } catch (e) {
    console.warn('Could not fetch extension info:', e);
  }
  // Fallback to relative or current folder
  cachedExtensionPath = 'openhydro/extension';
  pathDisplay.innerText = 'openhydro/extension';
}

function closeExtensionModal() {
  document.getElementById('extension-modal').classList.add('hidden');
}

async function openExtensionFolderInExplorer() {
  try {
    const res = await fetch('/api/extension/open_folder', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data && data.status === 'success') {
      showToast('📂 تم فتح مجلد الإضافة في مستكشف الملفات بنجاح!', 'success');
      return;
    }
    showToast('تعذر فتح المجلد تلقائياً: ' + (data.message || data.detail || 'يرجى فتح المجلد يدوياً من مساره'), 'warning');
  } catch (e) {
    showToast('حدث خطأ أثناء فتح المجلد: ' + e, 'error');
  }
}

function copyExtensionPath() {
  const path = cachedExtensionPath || document.getElementById('extension-folder-path').innerText;
  if (!path) return;
  navigator.clipboard.writeText(path).then(() => {
    showToast('📋 تم نسخ مسار مجلد الإضافة بنجاح!', 'success');
  }).catch(() => {
    showToast('تعذر النسخ إلى الحافظة', 'error');
  });
}


// --- OpenHydro Intelligent Auto-Updater Controller ---
let currentUpdateInfo = null;
let updateDownloadTaskId = null;

async function checkAppUpdates(showModal = false) {
  const icon = document.getElementById('nav-update-icon');
  if (icon) icon.classList.add('animate-spin');

  if (showModal) {
    openUpdateModal();
    renderUpdateChecking();
  }

  try {
    const res = await fetch('/api/updates/check');
    if (!res.ok) throw new Error('تعذر الاتصال بخوادم التحديث');
    const data = await res.json();
    currentUpdateInfo = data;

    const badge = document.getElementById('nav-update-badge');
    if (data.has_update) {
      if (badge) badge.classList.remove('hidden');
      if (showModal) {
        renderUpdateAvailable(data);
      } else {
        showToast(`🚀 يتوفر تحديث جديد: OpenHydro v${data.latest_version}! اضغط على زر التحديثات للترقية.`, 'info', 7000);
      }
    } else {
      if (badge) badge.classList.add('hidden');
      if (showModal) {
        renderUpdateUpToDate(data);
      }
    }
  } catch (e) {
    if (showModal) {
      renderUpdateError(e.message);
    }
  } finally {
    if (icon) icon.classList.remove('animate-spin');
    lucide.createIcons();
  }
}

function openUpdateModal() {
  document.getElementById('update-modal').classList.remove('hidden');
}

function closeUpdateModal() {
  document.getElementById('update-modal').classList.add('hidden');
}

function renderUpdateChecking() {
  const body = document.getElementById('update-modal-body');
  const actions = document.getElementById('update-action-container');
  body.innerHTML = `
    <div class="flex flex-col items-center justify-center py-8 text-center space-y-3">
      <div class="w-12 h-12 rounded-full border-2 border-cyan-500/20 border-t-cyan-400 animate-spin flex items-center justify-center"></div>
      <p class="text-sm font-semibold text-gray-200">جاري فحص خوادم GitHub للبحث عن إصدارات جديدة...</p>
      <p class="text-xs text-gray-400">التحقق من حزم التحديثات والتحسينات الرسمية</p>
    </div>
  `;
  actions.innerHTML = '';
  lucide.createIcons();
}

function renderUpdateUpToDate(data) {
  const body = document.getElementById('update-modal-body');
  const actions = document.getElementById('update-action-container');
  body.innerHTML = `
    <div class="flex flex-col items-center justify-center py-6 text-center space-y-3">
      <div class="w-14 h-14 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400 shadow-lg shadow-emerald-500/10">
        <i data-lucide="check-circle" class="w-8 h-8"></i>
      </div>
      <div>
        <h4 class="text-base font-bold text-white">أنت تستخدم أحدث إصدار بالفعل!</h4>
        <p class="text-xs text-emerald-400 font-mono mt-1">OpenHydro v${data.current_version}</p>
      </div>
      <p class="text-xs text-gray-400 max-w-sm">نظامك محدث بالكامل ومزود بأحدث محركات التجزئة 32-Thread وصيغ الفيديو وتكامل Google Drive وفيسبوك.</p>
    </div>
  `;
  actions.innerHTML = `
    <a href="${data.html_url}" target="_blank" class="px-4 py-2 rounded-xl text-xs font-semibold text-gray-300 hover:text-white glass-panel hover:bg-white/10 transition-all flex items-center gap-1.5">
      <i data-lucide="github" class="w-3.5 h-3.5"></i>
      <span>صفحة الإصدارات على GitHub</span>
    </a>
  `;
  lucide.createIcons();
}

function renderUpdateAvailable(data) {
  const body = document.getElementById('update-modal-body');
  const actions = document.getElementById('update-action-container');

  const notesFormatted = escapeHtml(data.release_notes || 'تحديث يتضمن تحسينات عامة للأداء واستقرار التنزيل.')
    .replace(/^### (.*$)/gim, '<h5 class="font-bold text-xs text-cyan-300 mt-2">$1</h5>')
    .replace(/^## (.*$)/gim, '<h4 class="font-bold text-sm text-cyan-200 mt-2">$1</h4>')
    .replace(/^\* (.*$)/gim, '<li class="ml-2 text-gray-300">$1</li>')
    .replace(/^- (.*$)/gim, '<li class="ml-2 text-gray-300">$1</li>');

  body.innerHTML = `
    <div class="space-y-4">
      <div class="p-3.5 rounded-xl bg-cyan-950/30 border border-cyan-500/30 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center text-white shadow-md shadow-cyan-500/30">
            <i data-lucide="sparkles" class="w-5 h-5"></i>
          </div>
          <div>
            <h4 class="text-sm font-bold text-white">${escapeHtml(data.release_name)}</h4>
            <div class="flex items-center gap-2 mt-0.5 text-xs">
              <span class="text-gray-400 font-mono">الإصدار الحالي: v${data.current_version}</span>
              <span class="text-gray-600">•</span>
              <span class="text-emerald-400 font-bold font-mono">الجديد: v${data.latest_version}</span>
            </div>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[11px] font-bold">
          تحديث متاح ⚡
        </span>
      </div>

      <div class="space-y-1.5">
        <label class="block text-xs font-semibold text-gray-300">ملاحظات الإصدار والتغييرات:</label>
        <div class="max-h-40 overflow-y-auto p-3 rounded-xl bg-slate-950/50 border border-white/5 text-xs text-gray-300 space-y-1 font-sans">
          ${notesFormatted}
        </div>
      </div>

      <div id="update-download-progress-container" class="hidden space-y-2 p-3 rounded-xl bg-white/5 border border-white/10">
        <div class="flex items-center justify-between text-xs">
          <span id="update-progress-status" class="text-cyan-400 font-medium">جاري تنزيل ملف التحديث عبر محرك Turbo...</span>
          <span id="update-progress-percent" class="font-mono text-gray-300">0%</span>
        </div>
        <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
          <div id="update-progress-bar" class="h-full bg-gradient-to-r from-cyan-500 to-blue-600 transition-all duration-200" style="width: 0%"></div>
        </div>
      </div>
    </div>
  `;

  actions.innerHTML = `
    <a href="${data.html_url}" target="_blank" class="px-3 py-2 rounded-xl text-xs font-semibold text-gray-300 hover:text-white glass-panel hover:bg-white/10 transition-all flex items-center gap-1.5">
      <i data-lucide="github" class="w-3.5 h-3.5"></i>
      <span>عرض في GitHub</span>
    </a>
    <button id="btn-start-update" onclick="startAutoUpdate()" class="px-4 py-2 rounded-xl text-xs font-bold text-white bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 shadow-lg shadow-cyan-500/25 transition-all flex items-center gap-2 hover:scale-105 active:scale-95">
      <i data-lucide="download" class="w-4 h-4"></i>
      <span>تحميل وتثبيت التحديث الآن</span>
    </button>
  `;
  lucide.createIcons();
}

function renderUpdateError(msg) {
  const body = document.getElementById('update-modal-body');
  const actions = document.getElementById('update-action-container');
  body.innerHTML = `
    <div class="flex flex-col items-center justify-center py-6 text-center space-y-3">
      <div class="w-12 h-12 rounded-2xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400">
        <i data-lucide="alert-triangle" class="w-6 h-6"></i>
      </div>
      <div>
        <h4 class="text-sm font-bold text-white">تعذر فحص التحديثات</h4>
        <p class="text-xs text-gray-400 mt-1">${msg}</p>
      </div>
    </div>
  `;
  actions.innerHTML = `
    <button onclick="checkAppUpdates(true)" class="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-white/10 hover:bg-white/20 transition-all flex items-center gap-1.5">
      <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
      <span>إعادة المحاولة</span>
    </button>
  `;
  lucide.createIcons();
}

async function startAutoUpdate() {
  if (!currentUpdateInfo || !currentUpdateInfo.download_url) return;

  const btn = document.getElementById('btn-start-update');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>جاري بدء التنزيل...</span>`;
    lucide.createIcons();
  }

  const progContainer = document.getElementById('update-download-progress-container');
  if (progContainer) progContainer.classList.remove('hidden');

  try {
    const res = await fetch('/api/updates/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        download_url: currentUpdateInfo.download_url,
        filename: currentUpdateInfo.asset_name || null
      })
    });
    if (!res.ok) throw new Error('فشل بدء تنزيل ملف التحديث');
    const task = await res.json();
    updateDownloadTaskId = task.id;

    trackUpdateTask(task.id);
  } catch (e) {
    showToast(`خطأ في التحديث: ${e.message}`, 'error');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span>إعادة المحاولة</span>`;
    }
  }
}

async function trackUpdateTask(taskId) {
  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/tasks/${taskId}`);
      if (!res.ok) {
        clearInterval(interval);
        return;
      }
      const task = await res.json();

      const pct = Math.round(task.progress_percent || 0);
      const bar = document.getElementById('update-progress-bar');
      const pctText = document.getElementById('update-progress-percent');
      const statusText = document.getElementById('update-progress-status');

      if (bar) bar.style.width = `${pct}%`;
      if (pctText) pctText.innerText = `${pct}% (${formatBytes(task.downloaded_bytes)} / ${formatBytes(task.total_bytes)})`;

      if (task.status === 'completed') {
        clearInterval(interval);
        if (statusText) statusText.innerText = '✅ اكتمل تنزيل التحديث بنجاح!';
        const btn = document.getElementById('btn-start-update');
        if (btn) {
          btn.disabled = false;
          btn.className = 'px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 shadow-lg shadow-emerald-500/25 transition-all flex items-center gap-2 hover:scale-105 active:scale-95 animate-pulse';
          btn.innerHTML = `<i data-lucide="play" class="w-4 h-4 fill-white"></i><span>تثبيت التحديث وإعادة التشغيل الآن 🚀</span>`;
          btn.onclick = () => launchUpdateInstaller(taskId);
          lucide.createIcons();
        }
      } else if (task.status === 'error') {
        clearInterval(interval);
        if (statusText) statusText.innerText = '❌ فشل تنزيل ملف التحديث';
      }
    } catch (e) {
      clearInterval(interval);
    }
  }, 500);
}

async function launchUpdateInstaller(taskId) {
  try {
    const res = await fetch('/api/updates/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('🚀 تم إطلاق برنامج التثبيت بنجاح! سيتم إغلاق التطبيق للتحديث...', 'success', 5000);
      setTimeout(() => {
        window.close();
      }, 2000);
    } else {
      showToast('تعذر فتح برنامج التثبيت تلقائياً، يمكنك فتحه يدوياً من مجلد التنزيلات.', 'warning');
    }
  } catch (e) {
    showToast(`خطأ أثناء التثبيت: ${e.message}`, 'error');
  }
}

// Mobile Remote Dashboard & QR
async function openMobileRemoteModal() {
  const modal = document.getElementById('mobile-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  lucide.createIcons();

  try {
    const res = await fetch('/api/system/remote_info');
    const data = await res.json();
    const urlText = document.getElementById('mobile-remote-url-text');
    const qrImg = document.getElementById('mobile-qr-image');
    // Phone control is off by default; it also needs a restart to start listening on the network
    const blocked = !data.enabled
      ? 'التحكم من الموبايل متوقف. فعّله من الإعدادات.'
      : (!data.listening ? 'أعد تشغيل OpenHydro لتفعيل التحكم من الموبايل.' : null);
    if (qrImg) qrImg.classList.toggle('hidden', !!blocked);
    if (urlText) urlText.innerText = blocked || data.url;
    if (blocked) return;
    // Generated locally: the URL carries the remote-control token and must not go to a third-party QR service
    if (qrImg && typeof qrcode === 'function') {
      const qr = qrcode(0, 'M');
      qr.addData(data.url);
      qr.make();
      qrImg.src = qr.createDataURL(6, 2);
    }
  } catch (e) {
    console.error('Failed to load remote info:', e);
  }
}

function closeMobileRemoteModal() {
  const modal = document.getElementById('mobile-modal');
  if (modal) modal.classList.add('hidden');
}

function copyRemoteUrl() {
  const urlText = document.getElementById('mobile-remote-url-text');
  if (urlText) {
    navigator.clipboard.writeText(urlText.innerText);
    showToast('تم نسخ رابط التحكم بالموبايل بنجاح!', 'success');
  }
}
