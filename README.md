# ⚡ OpenHydro (مدير التنزيل العصري فائق السرعة)

<div align="center">

![OpenHydro Banner](assets/icon.png)

**بديل IDM العصري فائق السرعة والمجاني ومفتوح المصدر لكافة المنصات**  
*Modern, High-Speed, Dynamic Segmentation Download Manager (IDM Alternative) for Windows, Linux & Android*

[![GitHub Release](https://img.shields.io/github/v/release/openhydro/openhydro?color=06b6d4&style=for-the-badge)](https://github.com/openhydro/openhydro/releases)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Android-3b82f6?style=for-the-badge)](https://github.com/openhydro/openhydro)
[![License](https://img.shields.io/badge/License-MIT-10b981?style=for-the-badge)](LICENSE)

</div>

---

## 🌟 المميزات الرئيسية (Key Features)

- 🚀 **محرك تجزئة ديناميكي فائق السرعة (Dynamic Segmentation Turbo):**
  - تقسيم الملفات وتنزيلها عبر **ما يصل إلى 32 مسار متوازي (Threads)** لتحقيق أقصى استغلال لسرعة الإنترنت لديك.
- 🎬 **زر التقاط الفيديوهات العائم (IDM Floating Sniffer):**
  - زر عائم ذكي فوق أي مشغل فيديو (يوتيوب، فيسبوك، تيك توك، تويتر/إكس، وغيرها).
  - اختيار مباشر بين دقة الفيديو (4K / 2K / 1080p / 720p / 480p).
- 🎵 **استخراج الصوت عالي الدقة (Audio Extraction):**
  - استخراج الصوت مباشرة بصيغة **MP3 عالية الجودة (320 kbps)** أو بصوت نقي **M4A**.
- 📝 **تفريغ النصوص والترجمة (Subtitles & Transcripts):**
  - تحميل ملف الترجمة والنصوص **SRT**.
  - تفريغ محتوى الفيديو بالكامل كملف نصي **TXT**.
- 📋 **تحميل قوائم التشغيل كاملة (Batch Playlist Downloader):**
  - تحميل أي Playlist كاملة بضغطة زر مثل Snaptube و Vidmate مع ترقيم الفيديوهات تلقائياً وحفظها في مجلد مخصص أو تحويلها لألبوم صوتي.
- ⏱️ **العمل بجوار الساعة (System Tray & Auto-Startup):**
  - تصغير سلس بجوار ساعة الويندوز عند الضغط على (X)، مع قائمة كليك يمين للإغلاق النهائي.
  - إمكانية البدء التلقائي مع الويندوز ليكون جاهزاً دائماً لاصطياد الروابط.

---

## 📦 التثبيت والتحميل (Downloads & Installation)

### 🪟 1. نظام ويندوز (Windows 10 / 11)
- **برنامج التثبيت الرسمي (Windows Installer):**  
  قم بتحميل وتثبيت [`OpenHydro_Setup_v1.0.0.exe`](https://github.com/openhydro/openhydro/releases) بضغطة واحدة، وسيتم تثبيته وإنشاء اختصارات سطح المكتب وقائمة ابدأ.
- **النسخة المحمولة (Portable Zip):**  
  قم بفك ضغط [`OpenHydro-v1.0.0-windows-x64-portable.zip`](https://github.com/openhydro/openhydro/releases) وتشغيل `OpenHydro.exe` مباشرة بدون تثبيت.

---

### 🐧 2. نظام لينكس (Linux - Ubuntu / Debian / Fedora / Arch)
#### التثبيت المباشر بأمر واحد (One-Line Installer):
```bash
curl -sSL https://raw.githubusercontent.com/openhydro/openhydro/main/installer-linux.sh | bash
```

#### التشغيل المحمول (Portable Package):
```bash
# قم بفك ضغط الحزمة
tar -xzvf OpenHydro-linux-x86_64.tar.gz
cd OpenHydro
./openhydro.sh
```

---

### 📱 3. نظام أندرويد (Android)
- **طريقة PWA (الأسرع والأسهل):**
  افتح المتصفح في هاتفك واضغط على (⋮) ثم **"تثبيت التطبيق" (Install App)** أو **"إضافة للشاشة الرئيسية"**.
- **طريقة Termux (تشغيل محلي كامل):**
  ```bash
  pkg update -y && pkg install python ffmpeg git -y
  git clone https://github.com/openhydro/openhydro.git
  cd openhydro && pip install -r requirements.txt
  python run.py --headless
  ```
- **حزمة APK:** متوفر كود مشروع أندرويد في مجلد `android/` للتجميع المباشر.

---

## 🌐 إضافة المتصفح (Browser Extension)
1. افتح صفحة الإضافات في متصفحك: `chrome://extensions` أو `edge://extensions`.
2. قم بتفعيل **Developer Mode (وضع مطور البرامج)** في الزاوية العلوية.
3. اضغط على **Load unpacked (تحميل إضافة لم يتم حزمها)**.
4. اختر مجلد `openhydro/extension`.

---

## 🛠️ البناء من المصدر (Build from Source)
```bash
# استنساخ المشروع
git clone https://github.com/openhydro/openhydro.git
cd openhydro

# إنشاء وتفعيل البيئة الافتراضية
python -m venv .venv
# على ويندوز:
.venv\Scripts\activate
# على لينكس:
source .venv/bin/activate

# تثبيت المتطلبات
pip install -r requirements.txt

# تشغيل التطبيق
python run.py
```

---

## 📄 الترخيص (License)
مشروع OpenHydro مرخص تحت رخصة **MIT License** المفتوحة المصدر.
