const OPENHYDRO_API = "http://127.0.0.1:8765/api/system";

// The page open in this tab (its cookies go with a link from it: see shareCookies in background.js)
let activePage = null;

async function checkStatus() {
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");

  try {
    const res = await fetch(OPENHYDRO_API);
    if (res.ok) {
      const data = await res.json();
      dot.style.background = "#10b981";
      text.innerText = `متصل بالمحرك (${data.active_downloads} تنزيلات نشطة)`;
    } else {
      throw new Error();
    }
  } catch (e) {
    dot.style.background = "#ef4444";
    text.innerText = "المحرك غير نشط (تأكد من فتح OpenHydro)";
  }
}

document.getElementById("send-btn").addEventListener("click", async () => {
  const input = document.getElementById("link-input");
  const url = input.value.trim();
  if (!url) return;

  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    alert("يرجى إدخال رابط يبدأ بـ http:// أو https://");
    return;
  }

  // Opens OpenHydro's add dialog with the link: it shows what the link is (video qualities, file size).
  // Through background.js, which shares the site's cookies first.
  let res = null;
  try {
    res = await chrome.runtime.sendMessage({ action: "open_add_dialog", url, page: activePage });
  } catch (e) {
    // the background worker could not be reached
  }
  if (res && res.success) {
    window.close();  // the choice (quality, name) is made in OpenHydro's window
  } else {
    alert("تعذر الاتصال بالتطبيق");
  }
});

// Automatic capture of browser downloads (on by default, see background.js)
const captureToggle = document.getElementById("capture-toggle");
chrome.storage.local.get({ captureEnabled: true }, (settings) => {
  captureToggle.checked = settings.captureEnabled;
});
captureToggle.addEventListener("change", () => {
  chrome.storage.local.set({ captureEnabled: captureToggle.checked });
});

// Auto grab active tab URL
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs[0] && tabs[0].url && tabs[0].url.startsWith("http")) {
    activePage = tabs[0].url;
    document.getElementById("link-input").value = tabs[0].url;
  }
});

checkStatus();

