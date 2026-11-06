const OPENHYDRO_API = "http://127.0.0.1:8765/api/system";
// Opens OpenHydro's add dialog with the link: it shows what the link is (video qualities, file size)
const ADD_DIALOG_API = "http://127.0.0.1:8765/api/app/add_dialog";

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

  try {
    const res = await fetch(ADD_DIALOG_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url })
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    window.close();  // the choice (quality, name) is made in OpenHydro's window
  } catch (e) {
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
    document.getElementById("link-input").value = tabs[0].url;
  }
});

checkStatus();

