# ADR-0005: هویت ترک = file_unique_id؛ dedup دولایه

- وضعیت: پذیرفته

## تصمیم
- `tracks.file_unique_id UNIQUE` — لایهٔ قطعی.
- `file_id` همراه با `bot_id` و `file_id_updated_at` ذخیره می‌شود و قابل refresh است.
- لایهٔ فازی: `dedup_bucket` (artist|title|duration/3) + جست‌وجو در ۳ سطل مجاور + `similarity ≥ 0.88` + `|Δduration| ≤ 2`.
- ترک‌های تکراری حذف نمی‌شوند؛ `canonical_track_id` می‌گیرند، چون هر کدام منبع پخش جایگزین هستند.
- `(channel_id, message_id)` در `channel_tracks` برای ترمیم file_reference نگه داشته می‌شود.

## پیامدها
- آستانهٔ dedup در `feature_flags` قابل تنظیم است؛ پنل ادمین صفحهٔ «ادغام‌های مشکوک» دارد.
- کوئری‌های سمت کاربر همیشه روی `coalesce(canonical_track_id, id)` کار می‌کنند.
