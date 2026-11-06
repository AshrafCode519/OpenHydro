// OpenHydro download window: live progress of one download (IDM style), opened by the desktop app

const taskId = new URLSearchParams(window.location.search).get('task');
let task = null;
let shownIcon = null;
let lastTitle = '';

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  queued: 'في الانتظار (يبدأ عند انتهاء تنزيل جارٍ)',
  paused: 'متوقف مؤقتاً',
  error: 'خطأ في التنزيل',
  completed: 'مكتمل',
};

function setTitle(title) {
  if (title === lastTitle) return;
  lastTitle = title;
  document.title = title;
  // The native window's title (taskbar), like IDM's "45% file.zip"
  if (window.pywebview && window.pywebview.api) window.pywebview.api.set_title(title);
}

function render() {
  if (!task) return;
  const done = task.status === 'completed';
  const active = task.status === 'downloading';
  const percent = Math.floor(task.progress_percent || 0);

  $('dw-name').textContent = task.filename;
  $('dw-name').title = task.filename;
  $('dw-url').textContent = task.url;
  $('dw-url').title = task.url;
  const icon = getCategoryIcon(task.category);
  if (icon !== shownIcon) {
    shownIcon = icon;
    $('dw-icon').innerHTML = `<i data-lucide="${icon}" class="w-5 h-5"></i>`;
    lucide.createIcons({ root: $('dw-icon') });
  }

  $('dw-progress').classList.toggle('hidden', done);
  $('dw-complete').classList.toggle('hidden', !done);

  if (done) {
    $('dw-complete-size').textContent = formatBytes(task.total_bytes || task.downloaded_bytes);
    $('dw-path').textContent = task.save_path;
    $('dw-path').title = task.save_path;
    setTitle(`اكتمل: ${task.filename}`);
  } else {
    $('dw-status').textContent = task.phase || (active
      ? `جاري التنزيل (${task.num_connections} اتصالات)`
      : (STATUS_TEXT[task.status] || task.status));
    $('dw-status').className = 'font-semibold ' + (task.status === 'error' ? 'text-rose-400'
      : task.status === 'paused' ? 'text-amber-400' : 'text-cyan-400');
    $('dw-size').textContent = task.total_bytes ? formatBytes(task.total_bytes) : 'غير معروف';
    $('dw-done').textContent = `${formatBytes(task.downloaded_bytes)} (${task.progress_percent}%)`;
    $('dw-speed').textContent = active ? formatSpeed(task.speed) : '—';
    $('dw-eta').textContent = active ? formatETA(task.eta_seconds) : '—';
    $('dw-resumable').textContent = task.supports_ranges || task.is_torrent ? 'نعم' : 'لا';
    $('dw-bar').style.width = `${task.progress_percent || 0}%`;
    $('dw-segments').innerHTML = (task.chunks || []).map(c => `
      <div class="segment-cell ${escapeHtml(c.status)}"><div class="segment-fill" style="width: ${c.progress_percent}%;"></div></div>
    `).join('');
    // On an error its message takes the segments' place (the window has a fixed size)
    const failed = task.status === 'error' && !!task.error_message;
    $('dw-segments').classList.toggle('hidden', failed);
    const message = $('dw-message');
    message.classList.toggle('hidden', !failed);
    message.textContent = task.error_message || '';
    message.title = task.error_message || '';
    setTitle(`${percent}% ${task.filename}`);
  }

  $('dw-pause').classList.toggle('hidden', !(active || task.status === 'queued'));
  $('dw-resume').classList.toggle('hidden', !(task.status === 'paused' || task.status === 'error'));
  $('dw-cancel').classList.toggle('hidden', done);
  $('dw-open').classList.toggle('hidden', !done);
  $('dw-folder').classList.toggle('hidden', !done);
  $('dw-close').textContent = done ? 'إغلاق' : 'إخفاء';

  // This download's own speed limit (left alone while the user is choosing one)
  const limit = $('dw-limit');
  if (document.activeElement !== limit) {
    const value = String(task.speed_limit_kbps || 0);
    if (![...limit.options].some(o => o.value === value)) {
      limit.add(new Option(`${formatSpeed(task.speed_limit_kbps * 1024)}`, value));
    }
    limit.value = value;
  }
}

// It finished while this window was open (not: the window was opened on a finished download)
function onFinished() {
  const openIt = $('dw-open-when-done').checked;
  const closeIt = $('dw-close-when-done').checked;
  if (openIt) call('open_file', closeIt);
  else if (closeIt) setTimeout(closeWindow, 1000);
}

async function setDownloadLimit(kbps) {
  try {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/speed_limit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_kbps: parseInt(kbps, 10) }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'HTTP ' + res.status);
  } catch (e) {
    const message = $('dw-message');
    message.textContent = 'تعذر تغيير حد السرعة: ' + e.message;
    message.classList.remove('hidden');
  }
}

// The two "when it finishes" choices carry over to the next download's window
const CHOICES_KEY = 'openhydro.downloadWindow.whenDone';

function rememberChoices() {
  try {
    localStorage.setItem(CHOICES_KEY, JSON.stringify({
      open: $('dw-open-when-done').checked, close: $('dw-close-when-done').checked,
    }));
  } catch (e) {
    // Storage unavailable: the choices only last for this window
  }
}

function restoreChoices() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHOICES_KEY) || '{}');
    $('dw-open-when-done').checked = !!saved.open;
    $('dw-close-when-done').checked = !!saved.close;
  } catch (e) {
    // Nothing saved, or storage unavailable
  }
}

function showGone() {
  // Cancelled here or removed from the list: nothing left to show
  task = null;
  $('dw-progress').classList.remove('hidden');
  $('dw-complete').classList.add('hidden');
  $('dw-status').textContent = 'تم إلغاء التنزيل';
  $('dw-status').className = 'font-semibold text-gray-400';
  for (const id of ['dw-pause', 'dw-resume', 'dw-cancel', 'dw-open', 'dw-folder']) $(id).classList.add('hidden');
  $('dw-close').textContent = 'إغلاق';
  setTimeout(closeWindow, 1500);
}

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
  socket.onmessage = (event) => {
    const { type, data } = JSON.parse(event.data);
    if (type === 'snapshot') {
      task = data.find(t => t.id === taskId) || null;
      if (task) render(); else showGone();
    } else if (data && data.id === taskId) {
      if (type === 'task_cancelled') {
        showGone();
      } else if (type.startsWith('task_')) {
        const finishedNow = task && task.status !== 'completed' && data.status === 'completed';
        task = data;
        render();
        if (finishedNow) onFinished();
      }
    }
  };
  socket.onclose = () => setTimeout(connect, 2000);
}

async function call(action, closeAfter = false) {
  try {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'HTTP ' + res.status);
    if (closeAfter) closeWindow();
  } catch (e) {
    const message = $('dw-message');
    message.textContent = 'تعذر تنفيذ الأمر: ' + e.message;
    message.classList.remove('hidden');
  }
}

const pauseDownload = () => call('pause');
const resumeDownload = () => call('resume');
const openFile = () => call('open_file', true);
const openFolder = () => call('open_folder', true);

function cancelDownload() {
  if (confirm('إلغاء هذا التنزيل؟')) call('cancel');
}

function closeWindow() {
  // Only the window closes: the download goes on (it is still in the main list)
  if (window.pywebview && window.pywebview.api) window.pywebview.api.close();
  else window.close();
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeWindow();
});

// pywebview's API arrives after the page: apply the title that was set before it
window.addEventListener('pywebviewready', () => {
  const title = lastTitle;
  lastTitle = '';
  setTitle(title);
});

lucide.createIcons();
restoreChoices();
connect();
