const OPENHYDRO_API = "http://127.0.0.1:8765/api/system";
const PUSH_API = "http://127.0.0.1:8765/api/remote/push";

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
    const res = await fetch(PUSH_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url, num_connections: 8, auto_start: true })
    });
    if (res.ok) {
      input.value = "";
      alert("تم إرسال الرابط للتحميل بنجاح!");
      window.close();
    }
  } catch (e) {
    alert("تعذر الاتصال بالتطبيق");
  }
});

// Auto grab active tab URL
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs[0] && tabs[0].url && tabs[0].url.startsWith("http")) {
    document.getElementById("link-input").value = tabs[0].url;
  }
});

checkStatus();

