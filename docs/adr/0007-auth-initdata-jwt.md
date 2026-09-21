# ADR-0007: احراز هویت با initData → JWT کوتاه‌مدت + refresh چرخشی

- وضعیت: پذیرفته

## تصمیم
- اعتبارسنجی HMAC روی initData طبق مستند تلگرام، با مقایسهٔ زمان‌ثابت و `auth_date ≤ 24h`.
- access JWT (EdDSA، ۱۵ دقیقه)؛ refresh سی‌روزه، چرخشی، هش‌شده، با `family_id` برای تشخیص استفادهٔ مجدد.
- ادمین: همان جریان + جدول `admin_users` + بررسی مجوز برای هر endpoint.
- impersonate: claim `act_as`، فقط‌خواندنی، و ثبت هر درخواست در audit_log.

## پیامدها
- JWT بدون state است → api کاملاً stateless و افقی مقیاس‌پذیر است.
- ابطال فوری (مثلاً بن کاربر) با یک denylist کوچک در Redis تا زمان انقضای access token.
