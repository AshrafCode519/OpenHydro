// OpenHydro Modern Dashboard Controller

let tasks = {};
let currentCategory = 'all';
let currentSearch = '';
let ws = null;
let probeTimeout = null;

// Utility Formatters
function formatBytes(bytes, decimals = 2) {
  if (bytes === 0 || !bytes) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function formatSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec <= 0) return '0.0 KB/s';
  if (bytesPerSec < 1024 * 1024) {
    return (bytesPerSec / 1024).toFixed(1) + ' KB/s';
  }
  return (bytesPerSec / (1024 * 1024)).toFixed(2) + ' MB/s';
}

function formatETA(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '--';
  if (seconds === 0) return '00:00';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hrs > 0) {
    return `${hrs}h ${mins}m`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// Category Icons
function getCategoryIcon(cat) {
  switch (cat) {
    case 'video': return 'video';
    case 'audio': return 'music';
    case 'compressed': return 'archive';
    case 'document': return 'file-text';
    case 'program': return 'box';
    default: return 'file';
  }
}

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
      <span class="truncate text-right leading-snug">${message}</span>
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

function showConfirm({ title = 'تأكيد الإجراء', message = 'هل أنت متأكد؟', okText = 'تأكيد', cancelText = 'إلغاء', onOk = null, isDanger = true }) {
  const modal = document.getElementById('app-confirm-modal');
  if (!modal) {
    if (confirm(message)) onOk?.();
    return;
  }

  document.getElementById('confirm-modal-title').innerText = title;
  document.getElementById('confirm-modal-msg').innerText = message;

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
      closeConfirmModal();
      if (cb) cb();
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
  } else if (type === 'task_added' || type === 'task_updated' || type === 'task_progress' || type === 'task_completed') {
    tasks[data.id] = data;
  } else if (type === 'task_cancelled') {
    delete tasks[data.id];
  }

  updateGlobalStats();
  renderTasks();
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
      diskElem.innerText = `المساحة الحرة: ${formatBytes(data.disk_free_bytes)}`;
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
    if (['video', 'audio', 'compressed', 'document', 'program'].includes(currentCategory)) {
      if (task.category !== currentCategory) return false;
    }

    // Search filter
    if (currentSearch && !task.filename.toLowerCase().includes(currentSearch)) {
      return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="flex flex-col items-center justify-center h-64 text-gray-500 gap-3">
        <i data-lucide="inbox" class="w-12 h-12 stroke-[1.5]"></i>
        <p class="text-sm">لا توجد تنزيلات متطابقة</p>
      </div>
    `;
    lucide.createIcons();
    return;
  }

  // Sort: downloading first, then by created_at desc
  filtered.sort((a, b) => {
    if (a.status === 'downloading' && b.status !== 'downloading') return -1;
    if (b.status === 'downloading' && a.status !== 'downloading') return 1;
    return b.created_at - a.created_at;
  });

  container.innerHTML = filtered.map(task => renderTaskCard(task)).join('');
  lucide.createIcons();
}

function renderTaskCard(task) {
  const isDownloading = task.status === 'downloading';
  const isCompleted = task.status === 'completed';
  const isPaused = task.status === 'paused';
  const isError = task.status === 'error';

  let statusBadge = '';
  if (isDownloading) {
    statusBadge = `
      <span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
        <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span>
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
  }

  // Segment visualization html
  const chunksHtml = task.chunks && task.chunks.length > 0 ? `
    <div class="mt-3 pt-3 border-t border-white/5">
      <div class="flex items-center justify-between text-[11px] text-gray-400 mb-1.5">
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
    <div class="glass-panel glass-panel-hover rounded-2xl p-4 transition-all">
      <div class="flex items-start justify-between gap-4">
        <!-- Icon & Title -->
        <div class="flex items-start gap-3 flex-1 min-w-0">
          <div class="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center shrink-0 border border-white/5 text-cyan-400">
            <i data-lucide="${getCategoryIcon(task.category)}" class="w-5 h-5"></i>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 flex-wrap">
              <h3 class="font-semibold text-sm text-gray-100 truncate" title="${task.filename}">${task.filename}</h3>
              ${statusBadge}
            </div>
            <p class="text-xs text-gray-400 truncate mt-0.5 font-mono">${task.url}</p>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex items-center gap-1.5 shrink-0">
          ${isDownloading ? `
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
        <div class="flex items-center justify-between text-xs text-gray-400 mt-2 font-mono">
          <div class="flex items-center gap-3">
            <span class="text-gray-200 font-semibold">${task.progress_percent}%</span>
            <span>${formatBytes(task.downloaded_bytes)} / ${task.total_bytes ? formatBytes(task.total_bytes) : 'غير محدد'}</span>
          </div>

          <div class="flex items-center gap-4">
            ${isDownloading ? `
              <span class="text-cyan-400 font-bold flex items-center gap-1">
                <i data-lucide="zap" class="w-3.5 h-3.5 fill-cyan-400"></i>
                ${formatSpeed(task.speed)}
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
  renderTasks();
}

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
  showConfirm({
    title: 'تأكيد حذف التنزيل',
    message: 'هل تريد بالتأكيد إزالة هذا التنزيل من القائمة؟',
    okText: 'نعم، حذف',
    cancelText: 'إلغاء',
    onOk: async () => {
      try {
        await fetch(`/api/tasks/${id}?delete_file=true`, { method: 'DELETE' });
        delete tasks[id];
        renderTasks();
        updateGlobalStats();
        showToast('تم حذف التنزيل بنجاح', 'info');
      } catch (e) {
        showToast('فشل حذف التنزيل: ' + e, 'error');
      }
    }
  });
}

async function pauseAllTasks() {
  Object.values(tasks).forEach(t => {
    if (t.status === 'downloading') pauseTask(t.id);
  });
}

async function resumeAllTasks() {
  Object.values(tasks).forEach(t => {
    if (t.status === 'paused') resumeTask(t.id);
  });
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

async function submitNewTask() {
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

// Initialization
window.addEventListener('DOMContentLoaded', () => {
  initWebSocket();
  fetchSystemInfo();
  setInterval(fetchSystemInfo, 10000);

  // Check URL params for auto-opening playlist modal
  const urlParams = new URLSearchParams(window.location.search);
  const playlistParam = urlParams.get('playlist');
  if (playlistParam) {
    openPlaylistModal(playlistParam);
  }
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
             data-index="${item.index}" data-url="${item.url}" data-download-url="${item.download_url || item.url}" data-is-gdrive="${item.is_gdrive ? 'true' : 'false'}" data-title="${encodeURIComponent(item.title)}" onchange="updatePlaylistSelectedCount()" checked>
      <span class="text-xs font-mono text-purple-400 w-6 shrink-0">${item.index}.</span>
      ${item.thumbnail ? `<img src="${item.thumbnail}" class="w-14 h-9 object-cover rounded border border-white/10 shrink-0" alt="">` : ''}
      <div class="flex-1 min-w-0">
        <p class="text-xs text-gray-200 font-medium truncate">${item.title}</p>
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
    if (res.ok) {
      const data = await res.json();
      if (data && data.status === 'success') {
        showToast('📂 تم فتح مجلد الإضافة في مستكشف الملفات بنجاح!', 'success');
        return;
      }
    }
    const errData = await res.json().catch(() => ({}));
    showToast('تعذر فتح المجلد تلقائياً: ' + (errData.message || errData.detail || 'يرجى فتح المجلد يدوياً'), 'warning');
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
