// OpenHydro Browser Extension Background Service Worker

const OPENHYDRO_BASE = "http://127.0.0.1:8765";
const OPENHYDRO_API = `${OPENHYDRO_BASE}/api/remote/push`;

// Create context menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "openhydro_download_link",
    title: "⚡ تنزيل بواسطة OpenHydro",
    contexts: ["link", "video", "audio", "image"]
  });
});

// Context menu click handler
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "openhydro_download_link") {
    const targetUrl = info.linkUrl || info.srcUrl;
    if (targetUrl) {
      sendToOpenHydro(targetUrl, tab ? tab.url : null);
    }
  }
});

// Handle messages from content script (floating grab button & qualities probe)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "probe_url") {
    fetch(`http://127.0.0.1:8765/api/probe?url=${encodeURIComponent(request.url)}`)
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => sendResponse(data))
      .catch((err) => {
        console.warn("OpenHydro server is not running or probe failed:", err.message);
        sendResponse({ error: true, notRunning: true });
      });
    return true; // Keep message channel open for async response
  }

  if (request.action === "download_media") {
    sendToOpenHydro(request.url, request.referrer, request.format_id)
      .then((ok) => sendResponse({ success: ok }))
      .catch(() => sendResponse({ success: false, notRunning: true }));
    return true; // Keep message channel open for async response
  }
});

// Send download task to local OpenHydro engine
async function sendToOpenHydro(url, referrer, format_id) {
  try {
    const response = await fetch(OPENHYDRO_API, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        url: url,
        referrer: referrer,
        num_connections: 32,
        auto_start: true,
        format_id: format_id || null
      })
    });

    if (response.ok) {
      chrome.notifications?.create({
        type: "basic",
        iconUrl: "icons/icon48.png",
        title: "OpenHydro",
        message: "تم إرسال الرابط بنجاح إلى OpenHydro وجاري التنزيل!"
      });
      return true;
    } else {
      console.warn("OpenHydro returned status:", response.status);
      return false;
    }
  } catch (error) {
    console.warn("OpenHydro is not reachable. Please make sure OpenHydro application is running.");
    return false;
  }
}

// --- Automatic capture of browser downloads (IDM style) ---
// Downloads of these types are taken over by OpenHydro (multi-connection, resumable). Anything the
// app cannot fetch itself (blob:/data: URLs) or that it fails to accept stays in the browser.

const CAPTURE_DEFAULTS = {
  captureEnabled: true,
  captureExtensions: [
    "exe", "msi", "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg", "apk", "deb", "rpm",
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mp3", "m4a", "flac", "wav", "aac", "ogg",
    "pdf", "epub", "torrent",
  ],
};

// URLs handed back to the browser after OpenHydro could not take them: skipped once, so the
// browser's own retry is not captured again
const bypassOnce = new Set();

let aliveCache = { at: 0, alive: false };

async function getCaptureSettings() {
  try {
    return { ...CAPTURE_DEFAULTS, ...(await chrome.storage.local.get(Object.keys(CAPTURE_DEFAULTS))) };
  } catch (e) {
    return { ...CAPTURE_DEFAULTS };
  }
}

function fileExtension(filename) {
  const name = (filename || "").split(/[\\/]/).pop();
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function shouldCapture(item, settings) {
  if (!settings.captureEnabled) return false;
  if (!/^https?:\/\//i.test(item.finalUrl || item.url || "")) return false;
  return settings.captureExtensions.includes(fileExtension(item.filename));
}

// Only cancel the browser's download once we know the app is there to take it
async function openHydroAlive() {
  if (Date.now() - aliveCache.at < 5000) return aliveCache.alive;
  let alive = false;
  try {
    const res = await fetch(`${OPENHYDRO_BASE}/api/version`, { signal: AbortSignal.timeout(1500) });
    alive = res.ok;
  } catch (e) {
    alive = false;
  }
  aliveCache = { at: Date.now(), alive };
  return alive;
}

async function sendCapturedDownload(item, url) {
  // The browser's cookies and user agent let OpenHydro fetch files behind a login
  const headers = { "User-Agent": navigator.userAgent };
  try {
    const cookies = await chrome.cookies.getAll({ url });
    if (cookies.length) headers["Cookie"] = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (e) {
    // no cookie access: continue without
  }
  try {
    const res = await fetch(`${OPENHYDRO_BASE}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        referrer: item.referrer || null,
        filename: (item.filename || "").split(/[\\/]/).pop() || null,
        num_connections: 16,
        auto_start: true,
        custom_headers: headers,
      }),
    });
    return res.ok;
  } catch (e) {
    return false;
  }
}

async function handleBrowserDownload(item, suggest) {
  const url = item.finalUrl || item.url;
  if (bypassOnce.delete(url)) {
    suggest();
    return;
  }
  const settings = await getCaptureSettings();
  if (!shouldCapture(item, settings) || !(await openHydroAlive())) {
    suggest();
    return;
  }

  // Take over: stop the browser's copy before it writes anything
  await chrome.downloads.cancel(item.id).catch(() => {});
  suggest();
  chrome.downloads.erase({ id: item.id }).catch(() => {});

  if (await sendCapturedDownload(item, url)) {
    chrome.notifications?.create({
      type: "basic",
      iconUrl: "icons/icon48.png",
      title: "OpenHydro",
      message: `⚡ يتم الآن تنزيل ${(item.filename || "").split(/[\\/]/).pop()} بواسطة OpenHydro`,
    });
  } else {
    // OpenHydro refused it: never lose the download, give it back to the browser
    bypassOnce.add(url);
    chrome.downloads.download({ url });
  }
}

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  handleBrowserDownload(item, suggest);
  return true; // suggest() is called asynchronously
});
