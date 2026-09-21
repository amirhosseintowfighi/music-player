# ADR-0008: پرداخت پشت پورت PaymentProvider با callback idempotent

- وضعیت: پذیرفته

## تصمیم
- پورت: `create_invoice / verify / refund`. adapter ها: Stars، Zarinpal، IDPay، NextPay، CardToCard.
- رجیستری از جدول `payment_providers` خوانده می‌شود؛ درگاه جدید بدون تغییر core اضافه می‌شود.
- idempotency با `UNIQUE (provider, provider_ref)`.
- verify همیشه با یک تماس مستقل سرور به درگاه انجام می‌شود.
- فعال‌سازی اشتراک داخل تراکنش با قفل ردیف کاربر؛ و `UNIQUE` روی «حداکثر یک اشتراک زنده برای هر کاربر».
- همهٔ رویدادهای مالی در audit_log.
- قیمت‌ها و محدودیت‌های پلن در جدول `plans` — بدون هیچ hardcode.

## یادداشت
«یک پورت با چند adapter» اینجا توجیه دارد: از روز اول پنج پیاده‌سازی واقعی داریم.
