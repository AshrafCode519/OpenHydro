// OpenHydro Browser Extension Background Service Worker

const OPENHYDRO_API = "http://127.0.0.1:8765/api/remote/push";

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
