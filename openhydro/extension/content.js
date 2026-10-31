// OpenHydro Video Sniffer Content Script (Floating IDM-Style Button & Multi-Option Menu)

(function () {
  "use strict";

  if (window.__openhydro_injected) return;
  window.__openhydro_injected = true;

  function showToast(message, isError = false) {
    const existing = document.querySelector(".openhydro-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.className = "openhydro-toast";
    if (isError) {
      toast.style.borderColor = "#ef4444";
      toast.style.boxShadow = "0 12px 30px rgba(0, 0, 0, 0.75), 0 0 20px rgba(239, 68, 68, 0.4)";
    }
    toast.innerHTML = `
      <span style="font-size: 16px;">${isError ? "⚠️" : "⚡"}</span>
      <span>${message}</span>
    `;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.style.transition = "opacity 0.4s ease, transform 0.4s ease";
      toast.style.opacity = "0";
      toast.style.transform = "translateY(12px)";
      setTimeout(() => toast.remove(), 400);
    }, 4000);
  }

  function getMediaUrl(video) {
    const host = window.location.hostname.toLowerCase();
    if (
      host.includes("youtube.com") ||
      host.includes("youtu.be") ||
      host.includes("tiktok.com") ||
      host.includes("facebook.com") ||
      host.includes("twitter.com") ||
      host.includes("x.com") ||
      host.includes("instagram.com") ||
      host.includes("vimeo.com") ||
      host.includes("dailymotion.com") ||
      host.includes("drive.google.com") ||
      host.includes("docs.google.com")
    ) {
      return window.location.href;
    }

    if (video.currentSrc && !video.currentSrc.startsWith("blob:")) {
      return video.currentSrc;
    }
    if (video.src && !video.src.startsWith("blob:")) {
      return video.src;
    }

    const source = video.querySelector("source");
    if (source && source.src && !source.src.startsWith("blob:")) {
      return source.src;
    }

    return window.location.href;
  }

  function triggerDownload(url, formatId, label) {
    chrome.runtime.sendMessage(
      {
        action: "download_media",
        url: url,
        referrer: window.location.href,
        format_id: formatId,
      },
      (res) => {
        if (res && res.success) {
          if (formatId === "audio_mp3" || formatId === "audio_m4a") {
            showToast(`🎵 جاري استخراج الصوت [${label}] بأقصى سرعة!`);
          } else if (formatId === "subtitle_srt" || formatId === "subtitle_txt") {
            showToast(`📝 جاري تنزيل النص والتفريغ [${label}] إلى مجلد التنزيلات!`);
          } else {
            showToast(`⚡ تم بدء تحميل الفيديو بدقة [${label}] عبر 32 مسار متوازي!`);
          }
        } else {
          showToast("تعذر الاتصال بـ OpenHydro. تأكد من تشغيل البرنامج!", true);
        }
      }
    );
  }

  function attachGrabButton(video) {
    if (video.dataset.openhydroAttached) return;

    const rect = video.getBoundingClientRect();
    if (rect.width < 140 && rect.height < 90) return;

    let container = video.parentElement;
    if (window.location.hostname.includes("youtube.com")) {
      const player = document.getElementById("movie_player") || document.querySelector(".html5-video-player");
      if (player) container = player;
    }

    if (!container) return;
    video.dataset.openhydroAttached = "true";

    const style = window.getComputedStyle(container);
    if (style.position === "static") {
      container.classList.add("openhydro-video-container-relative");
    }

    // Create wrapper
    const wrapper = document.createElement("div");
    wrapper.className = "openhydro-wrapper";

    // Create Floating Button
    const btn = document.createElement("div");
    btn.className = "openhydro-grab-btn";
    btn.title = "تحميل هذا الفيديو / استخراج الصوت / تنزيل النصوص";
    btn.innerHTML = `
      <div class="openhydro-grab-icon">⚡</div>
      <span class="openhydro-grab-text">تحميل هذا الفيديو</span>
      <span class="openhydro-grab-tag">دقة | صوت | نص</span>
      <span class="openhydro-grab-arrow">▼</span>
    `;

    let menu = null;
    let hideTimer = null;
    let isMenuOpen = false;
    let cachedQualities = null;

    function openMenu() {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (isMenuOpen && menu) return;

      isMenuOpen = true;
      btn.classList.add("openhydro-active");

      menu = document.createElement("div");
      menu.className = "openhydro-quality-menu";

      const mediaUrl = getMediaUrl(video);
      renderMenuContents(mediaUrl, cachedQualities);

      wrapper.appendChild(menu);

      // Mouse enter/leave handlers for menu
      menu.addEventListener("mouseenter", () => {
        if (hideTimer) {
          clearTimeout(hideTimer);
          hideTimer = null;
        }
      });
      menu.addEventListener("mouseleave", scheduleClose);

      // Probe in background if not cached yet
      if (!cachedQualities) {
        chrome.runtime.sendMessage(
          { action: "probe_url", url: mediaUrl },
          (response) => {
            if (response && response.qualities && response.qualities.length > 0) {
              cachedQualities = response.qualities;
              if (menu && isMenuOpen) {
                renderMenuContents(mediaUrl, cachedQualities);
              }
            }
          }
        );
      }
    }

    function closeMenu() {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      isMenuOpen = false;
      btn.classList.remove("openhydro-active");
      if (menu) {
        menu.remove();
        menu = null;
      }
    }

    function scheduleClose() {
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        closeMenu();
      }, 350);
    }

    function renderMenuContents(mediaUrl, probedQualities) {
      if (!menu) return;

      const header = `
        <div class="openhydro-menu-header">
          <div class="openhydro-menu-title">
            <span>⚡</span>
            <span>خيارات التنزيل (OpenHydro)</span>
          </div>
          <button class="openhydro-menu-close" title="إغلاق">✕</button>
        </div>
      `;

      let videoItems = [];
      let audioItems = [];
      let textItems = [];

      if (probedQualities && probedQualities.length > 0) {
        probedQualities.forEach((q) => {
          if (q.type === "audio" || q.is_audio) {
            audioItems.push(q);
          } else if (q.type === "subtitle" || q.type === "text" || q.is_subtitle || q.is_text) {
            textItems.push(q);
          } else {
            videoItems.push(q);
          }
        });
      }

      // Default fallback items if probe not ready or didn't provide full set
      if (videoItems.length === 0) {
        videoItems = [
          { label: "أقصى دقة متاحة (Best Quality)", format_id: "bestvideo+bestaudio/best", badge: "BEST", badgeClass: "openhydro-badge-best" },
          { label: "1080p (عالي الدقة FHD)", format_id: "bestvideo[height<=1080]+bestaudio/best[height<=1080]", badge: "1080p" },
          { label: "720p (دقة قياسية HD)", format_id: "bestvideo[height<=720]+bestaudio/best[height<=720]", badge: "720p" },
          { label: "480p (توفير البيانات SD)", format_id: "bestvideo[height<=480]+bestaudio/best[height<=480]", badge: "480p" },
        ];
      }

      if (audioItems.length === 0) {
        audioItems = [
          { label: "استخراج ملف صوتي عالي الدقة (MP3 - 320kbps)", format_id: "audio_mp3", badge: "MP3", badgeClass: "openhydro-badge-audio" },
          { label: "استخراج الصوت الأصلي النقي (M4A)", format_id: "audio_m4a", badge: "M4A", badgeClass: "openhydro-badge-audio" },
        ];
      }

      if (textItems.length === 0) {
        textItems = [
          { label: "تحميل ملف الترجمة والنصوص (SRT Subtitles)", format_id: "subtitle_srt", badge: "SRT", badgeClass: "openhydro-badge-sub" },
          { label: "تفريغ محتوى الفيديو كنص كامل (TXT Transcript)", format_id: "subtitle_txt", badge: "TXT", badgeClass: "openhydro-badge-text" },
        ];
      }

      let html = header;

      // Section 1: Video Resolutions
      html += `<div class="openhydro-menu-section openhydro-section-video">🎬 دقة الفيديو (Video Quality)</div>`;
      videoItems.forEach((v) => {
        const badge = v.badge || (v.height ? v.height + "p" : "MP4");
        const bClass = v.badgeClass || (v.height && v.height >= 1080 ? "openhydro-badge-best" : "");
        html += `
          <div class="openhydro-quality-item" data-format="${v.format_id}" data-label="${v.label}">
            <div class="openhydro-item-label">
              <span class="openhydro-item-icon">🎥</span>
              <span>${v.label}</span>
            </div>
            <span class="openhydro-badge ${bClass}">${badge}</span>
          </div>
        `;
      });

      // Section 2: Audio Only
      html += `<div class="openhydro-menu-section openhydro-section-audio">🎵 تحميل الصوت فقط (Audio Only)</div>`;
      audioItems.forEach((a) => {
        const badge = a.badge || (a.ext ? a.ext.toUpperCase() : "MP3");
        html += `
          <div class="openhydro-quality-item" data-format="${a.format_id}" data-label="${a.label}">
            <div class="openhydro-item-label">
              <span class="openhydro-item-icon">🎵</span>
              <span>${a.label}</span>
            </div>
            <span class="openhydro-badge openhydro-badge-audio">${badge}</span>
          </div>
        `;
      });

      // Section 3: Subtitles and Transcript
      html += `<div class="openhydro-menu-section openhydro-section-text">📝 تفريغ النصوص والترجمة (Subtitles & Text)</div>`;
      textItems.forEach((t) => {
        const isSrt = (t.format_id === "subtitle_srt" || t.ext === "srt");
        const badge = isSrt ? "SRT" : "TXT";
        const bClass = isSrt ? "openhydro-badge-sub" : "openhydro-badge-text";
        const icon = isSrt ? "📝" : "📄";
        html += `
          <div class="openhydro-quality-item" data-format="${t.format_id}" data-label="${t.label}">
            <div class="openhydro-item-label">
              <span class="openhydro-item-icon">${icon}</span>
              <span>${t.label}</span>
            </div>
            <span class="openhydro-badge ${bClass}">${badge}</span>
          </div>
        `;
      });

      // Section 4: Playlist if detected
      const isPlaylist = window.location.href.includes("list=") || mediaUrl.includes("list=");
      if (isPlaylist) {
        html += `
          <div class="openhydro-menu-section openhydro-section-playlist">📋 قائمة تشغيل كاملة (Playlist)</div>
          <div class="openhydro-quality-item openhydro-playlist-item" style="border: 1px dashed rgba(249, 115, 22, 0.45) !important; background: rgba(249, 115, 22, 0.1) !important;">
            <div class="openhydro-item-label">
              <span class="openhydro-item-icon">📋</span>
              <span style="color: #fed7aa !important; font-weight: 600;">تحميل القائمة كاملة دفعة واحدة (Snaptube Style)</span>
            </div>
            <span class="openhydro-badge openhydro-badge-playlist">PLAYLIST</span>
          </div>
        `;
      }

      menu.innerHTML = html;

      // Close button handler
      const closeBtn = menu.querySelector(".openhydro-menu-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          closeMenu();
        });
      }

      // Playlist item handler
      const plItem = menu.querySelector(".openhydro-playlist-item");
      if (plItem) {
        plItem.addEventListener("click", (e) => {
          e.stopPropagation();
          closeMenu();
          window.open(`http://127.0.0.1:8765/?playlist=${encodeURIComponent(window.location.href)}`, "_blank");
        });
      }

      // Download option items handler
      menu.querySelectorAll(".openhydro-quality-item:not(.openhydro-playlist-item)").forEach((item) => {
        item.addEventListener("click", (e) => {
          e.stopPropagation();
          const formatId = item.dataset.format;
          const label = item.dataset.label;
          closeMenu();
          triggerDownload(mediaUrl, formatId, label);
        });
      });
    }

    // Toggle menu on button click
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isMenuOpen) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    // Show menu on button hover (with delay on mouseleave)
    btn.addEventListener("mouseenter", openMenu);
    btn.addEventListener("mouseleave", scheduleClose);

    wrapper.appendChild(btn);
    container.appendChild(wrapper);
  }

  // Click outside to close
  document.addEventListener("click", (e) => {
    const activeMenus = document.querySelectorAll(".openhydro-quality-menu");
    activeMenus.forEach((m) => {
      if (!m.parentElement.contains(e.target)) {
        m.parentElement.querySelector(".openhydro-grab-btn")?.classList.remove("openhydro-active");
        m.remove();
      }
    });
  });

  function scanVideos() {
    const videos = document.querySelectorAll("video");
    videos.forEach((video) => {
      if (video.readyState >= 1 || video.currentTime > 0 || video.src || video.currentSrc) {
        attachGrabButton(video);
      } else {
        video.addEventListener("loadeddata", () => attachGrabButton(video), { once: true });
        video.addEventListener("play", () => attachGrabButton(video), { once: true });
      }
    });
  }

  scanVideos();

  const observer = new MutationObserver(() => {
    scanVideos();
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  setInterval(scanVideos, 2500);
})();
