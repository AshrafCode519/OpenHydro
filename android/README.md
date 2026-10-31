# OpenHydro for Android 📱

يمكن تشغيل OpenHydro على أجهزة أندرويد بعدة طرق مرنة وسريعة:

---

## 1. التثبيت المباشر كتطبيق ويب تقدمي (PWA - الطريقة الأسرع والأسهل)
واجهة OpenHydro مجهزة بالكامل كـ **Progressive Web App**:
1. افتح متصفح Chrome أو Edge على هاتفك الأندرويد.
2. ادخل على رابط السيرفر (مثلاً عبر الشبكة المحلية `http://<IP_ADDRESS>:8765`).
3. اضغط على قائمة المتصفح (⋮) واختر **"تثبيت التطبيق" (Install App)** أو **"إضافة إلى الشاشة الرئيسية" (Add to Home screen)**.
4. سيظهر تطبيق OpenHydro كأيقونة مستقلة على شاشتك الرئيسية ويعمل بملء الشاشة كتطبيق أصلي (Standalone).

---

## 2. التشغيل عبر بيئة Termux (محرك كامل على الهاتف)
يمكنك تشغيل محرك التنزيل فائق السرعة واستخراج الفيديوهات محلياً على هاتفك داخل Termux:
```bash
# تحديث الحزم وتثبيت بايثون و ffmpeg
pkg update -y && pkg install python ffmpeg git -y

# استنساخ المشروع
git clone https://github.com/openhydro/openhydro.git
cd openhydro

# تثبيت المتطلبات وتشغيل السيرفر
pip install -r requirements.txt
python run.py --headless
```
ثم افتح متصفح هاتفك على `http://localhost:8765`.

---

## 3. تجميع ملف APK أصلي (Native APK)
تم توفير ملفات مشروع أندرويد في مجلد `android/`:
1. افتح مجلد `android/` داخل **Android Studio**.
2. قم بعمل **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
3. سينتج لك ملف `app-release.apk` جاهز للتثبيت على أي جهاز أندرويد.

