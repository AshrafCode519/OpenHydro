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

    // Special handling for Facebook:
    if (host.includes("facebook.com") || host.includes("fb.watch")) {
      const path = window.location.pathname.toLowerCase();
      // If currently on an actual watch, reel, or videos permalink page:
      if (path.includes("/watch") || path.includes("/reel/") || path.includes("/videos/")) {
        return window.location.href;
      }

      // If playing in feed, group, or timeline, find enclosing post container
      let curr = video;
      for (let i = 0; i < 15 && curr; i++) {
        curr = curr.parentElement;
        if (!curr) break;

        const videoLink = curr.querySelector('a[href*="/videos/"], a[href*="/watch"], a[href*="/reel/"], a[href*="/share/v/"], a[href*="/share/r/"]');
        if (videoLink && videoLink.href) {
          return videoLink.href;
        }

        if (curr.getAttribute("role") === "article" || curr.dataset.pagelet) {
          const anyPostLink = curr.querySelector('a[href*="/posts/"], a[href*="/permalink/"], a[href*="fbid="]');
          if (anyPostLink && anyPostLink.href) {
            return anyPostLink.href;
          }
        }
      }

      // Fallback to direct video src if available and not blob
      if (video.currentSrc && !video.currentSrc.startsWith("blob:")) {
        return video.currentSrc;
      }
      if (video.src && !video.src.startsWith("blob:")) {
        return video.src;
      }

      return window.location.href;
    }

    if (
      host.includes("youtube.com") ||
      host.includes("youtu.be") ||
      host.includes("tiktok.com") ||
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

    // Detect platform for default positioning
    const isFacebook = window.location.hostname.includes("facebook.com") || window.location.hostname.includes("fb.watch");
    const savedPos = localStorage.getItem("openhydro_btn_pos");
    if (savedPos) {
      try {
        const parsed = JSON.parse(savedPos);
        if (parsed.top !== undefined && parsed.left !== undefined) {
          wrapper.style.setProperty("top", parsed.top, "important");
          wrapper.style.setProperty("left", parsed.left, "important");
        }
      } catch (e) {}
    } else if (isFacebook) {
      // Default safely below Facebook's top header controls (author name, volume, pause)
      wrapper.style.setProperty("top", "76px", "important");
      wrapper.style.setProperty("left", "16px", "important");
    } else {
      wrapper.style.setProperty("top", "14px", "important");
      wrapper.style.setProperty("left", "14px", "important");
    }

    // Create Floating Button
    const btn = document.createElement("div");
    btn.className = "openhydro-grab-btn";
    btn.title = "تحميل هذا الفيديو / استخراج الصوت / تنزيل النصوص (انقر للفتح، اسحب من ⠿ للتحريك)";
    btn.innerHTML = `
      <div class="openhydro-drag-handle" title="اسحب لتحريك الزر لأي مكان">⠿</div>
      <div class="openhydro-grab-icon">⚡</div>
      <span class="openhydro-grab-text">تحميل الفيديو</span>
      <span class="openhydro-grab-arrow">▼</span>
      <button class="openhydro-btn-min" title="تصغير الزر">−</button>
    `;

    // Drag and Drop Logic
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let wrapperStartX = 0;
    let wrapperStartY = 0;
    let hasMoved = false;

    const dragHandle = btn.querySelector(".openhydro-drag-handle");
    dragHandle.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      isDragging = true;
      hasMoved = false;
      dragStartX = e.clientX;
      dragStartY = e.clientY;
      const rect = wrapper.getBoundingClientRect();
      const parentRect = container.getBoundingClientRect();
      wrapperStartX = rect.left - parentRect.left;
      wrapperStartY = rect.top - parentRect.top;
      e.preventDefault();
      e.stopPropagation();
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const dx = e.clientX - dragStartX;
      const dy = e.clientY - dragStartY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        hasMoved = true;
      }
      if (hasMoved) {
        let newX = wrapperStartX + dx;
        let newY = wrapperStartY + dy;
        const cWidth = container.clientWidth || 300;
        const cHeight = container.clientHeight || 200;
        newX = Math.max(4, Math.min(newX, cWidth - wrapper.offsetWidth - 4));
        newY = Math.max(4, Math.min(newY, cHeight - wrapper.offsetHeight - 4));

        wrapper.style.setProperty("left", `${newX}px`, "important");
        wrapper.style.setProperty("top", `${newY}px`, "important");
      }
    });

    document.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        if (hasMoved) {
          try {
            localStorage.setItem("openhydro_btn_pos", JSON.stringify({
              top: wrapper.style.top,
              left: wrapper.style.left
            }));
          } catch (e) {}
        }
      }
    });

    // Minimize / Expand handler
    const minBtn = btn.querySelector(".openhydro-btn-min");
    minBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeMenu();
      wrapper.classList.toggle("openhydro-collapsed");
    });

    let menu = null;
    let hideTimer = null;
    let isMenuOpen = false;
    let cachedQualities = null;
    let lastMediaUrl = null;

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

      // Smart flip: if near bottom of viewport, open upwards
      const rect = wrapper.getBoundingClientRect();
      if (window.innerHeight - rect.bottom < 340 && rect.top > 320) {
        menu.style.top = "auto";
        menu.style.bottom = "calc(100% + 8px)";
      } else {
        menu.style.top = "calc(100% + 8px)";
        menu.style.bottom = "auto";
      }

      const mediaUrl = getMediaUrl(video);
      if (lastMediaUrl !== mediaUrl) {
        cachedQualities = null;
        lastMediaUrl = mediaUrl;
      }
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

    // Toggle menu on button click (Click-only, no annoying hover hijacking!)
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (hasMoved) {
        hasMoved = false;
        return;
      }
      if (wrapper.classList.contains("openhydro-collapsed")) {
        wrapper.classList.remove("openhydro-collapsed");
        return;
      }
      if (isMenuOpen) {
        closeMenu();
      } else {
        openMenu();
      }
    });

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
      if (video.dataset.openhydroAttached) return;
      if (video.readyState >= 1 || video.currentTime > 0 || video.src || video.currentSrc) {
        attachGrabButton(video);
      } else if (!video.dataset.openhydroListening) {
        video.dataset.openhydroListening = "true";
        video.addEventListener("loadeddata", () => attachGrabButton(video), { once: true });
        video.addEventListener("play", () => attachGrabButton(video), { once: true });
      }
    });
  }

  // Handle SPA transitions (YouTube, TikTok, Instagram)
  window.addEventListener("yt-navigate-finish", () => {
    document.querySelectorAll(".openhydro-wrapper").forEach((w) => w.remove());
    document.querySelectorAll("video").forEach((v) => {
      delete v.dataset.openhydroAttached;
      delete v.dataset.openhydroListening;
    });
    setTimeout(scanVideos, 300);
  });

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
