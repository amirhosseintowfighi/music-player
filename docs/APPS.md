# اپ اندروید و ویندوز

هر دو اپ همان نسخهٔ وب (`web/`) هستند، بسته‌بندی‌شده برای نصب. یعنی هر چیزی که روی سایت
دیپلوی شود، بدون آپدیت اپ، در اپ‌ها هم هست. کد جدا برای اپ‌ها لازم نیست.

| | چه چیزی | کجا منتشر می‌شود |
|---|---|---|
| اندروید | TWA (Trusted Web Activity) ساخته‌شده با Bubblewrap | کافه‌بازار، مایکت، لینک مستقیم APK، (Google Play با AAB) |
| ویندوز | PWA نصب‌شدنی + بستهٔ MSIX از PWABuilder | نصب از خود سایت، Microsoft Store، فایل MSIX |

> محدودیت: پرداخت با Telegram Stars فقط داخل تلگرام است. در اپ‌ها درگاه ایرانی و
> کارت‌به‌کارت کار می‌کند.

---

## اندروید

### ۱. یک بار: کلید امضا

```bash
bash apps/android/make-keystore.sh
```

دو secret چاپ می‌شود. در GitHub: **Settings → Secrets and variables → Actions** این‌ها را
اضافه کن:

- `ANDROID_KEYSTORE_B64`
- `ANDROID_KEYSTORE_PASSWORD`

فایل `apps/android/android.keystore` و رمزش را جای امن نگه دار. اگر گم شود، اپ دیگر آپدیت
نمی‌شود و باید با اسم جدید منتشرش کنی. این فایل در git نمی‌رود.

### ۲. ساخت

در GitHub: **Actions → android-app → Run workflow**. دامنه (پیش‌فرض
`noax.virgule.studio`) و نسخه را بده. خروجی‌ها روی همان اجرا قرار می‌گیرند:

- `app-release-signed.apk` برای بازار، مایکت و دانلود مستقیم
- `app-release-bundle.aab` برای Google Play

### ۳. حذف نوار آدرس (یک بار)

اثر انگشتی که آخر workflow چاپ می‌شود (`ANDROID_CERT_SHA256=…`) را در `.env` سرور بگذار و
دوباره دیپلوی کن:

```bash
bash scripts/deploy.sh --force
curl -s https://noax.virgule.studio/.well-known/assetlinks.json   # باید JSON برگرداند
```

بدون این فایل اپ کار می‌کند، ولی بالای صفحه نوار آدرس Chrome دیده می‌شود.

### نکته‌ها

- پخش در پس‌زمینه و کنترل از نوتیفیکیشن از طریق Chrome انجام می‌شود. روی بعضی گوشی‌های
  شیائومی و سامسونگ باید محدودیت باتری را برای اپ برداشت.
- Google Play احتمالاً به‌خاطر سیاست کپی‌رایت (موزیک بازنشرشده از کانال‌ها) رد می‌کند.
  قبل از ارسال به کافه‌بازار هم قوانین محتوای آن را بخوان.

---

## ویندوز

### نصب مستقیم (همین الان)

در Edge یا Chrome سایت را باز کن. در «بیشتر» دکمهٔ **«نصب نوکس روی این دستگاه»** هست.
اپ با آیکون خودش و در پنجرهٔ جدا باز می‌شود. روی مک و Chromebook هم همین است.

### فایل نصب یا Microsoft Store

1. به [pwabuilder.com](https://www.pwabuilder.com) برو و آدرس سایت را بده.
2. **Package for stores → Windows** را بزن.
   - برای فروشگاه: Publisher ID و Package ID از Partner Center مایکروسافت را وارد کن.
   - برای تست یا پخش مستقیم: گزینهٔ sideload/test package را انتخاب کن.
3. خروجی MSIX را نصب کن یا به Partner Center بفرست.

manifest سایت (`web/public/manifest.webmanifest`) برای این کار آماده است: اسم، توضیح،
آیکون‌ها، میان‌برها (جست‌وجو، کتابخانه، دانلودها) و نوار عنوان یکپارچه
(window-controls-overlay).
