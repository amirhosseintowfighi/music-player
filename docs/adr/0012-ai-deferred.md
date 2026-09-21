# ADR-0012: قابلیت‌های AI در فاز اول خاموش، پشت feature flag

- وضعیت: پذیرفته (تأیید کاربر)

## تصمیم
- پورت `AIProvider` (`embed`، `describe_to_playlist`) تعریف می‌شود؛ adapter فعلی `NullAIProvider` است.
- flag `ai_search` خاموش است؛ UI این قابلیت را برای پرمیوم با برچسب «به‌زودی» نشان می‌دهد.
- اسکیمای Meili از همین حالا جای فیلد embedding را دارد (Meili از hybrid search پشتیبانی می‌کند) تا فعال‌سازی بعدی به migration داده نیاز نداشته باشد.

## پیامدها
در فاز اول هیچ هزینهٔ متغیر AI نداریم. فعال‌سازی = یک adapter + کلید در env + روشن‌کردن flag.
