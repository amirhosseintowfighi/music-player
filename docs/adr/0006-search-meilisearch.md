# ADR-0006: Meilisearch برای سرچ؛ pg_trgm برای dedup و fallback

- وضعیت: پذیرفته

## تصمیم
- Meilisearch سرچ اصلی است: typo tolerance آماده، سبک (یک باینری، RAM کم)، و p95 زیر ۵۰ms در این مقیاس.
- فینگلیش: یک ترنسلیتراتور قطعی چندگونه در زمان ایندکس (`title_finglish`، `artist_finglish`) + `artists.aliases`.
- Meili منبع حقیقت نیست؛ sync افزایشی روی `tracks.updated_at` و بازسازی کامل با یک job.
- pg_trgm برای dedup، سرچ پنل ادمین، و fallback وقتی Meili از دسترس خارج است (کیفیت افت می‌کند ولی سرویس قطع نمی‌شود).

## گزینه‌های ردشده
- Elasticsearch: RAM و عملیات سنگین‌تر از بودجه؛ آنالیزر فارسی هم کار اضافه می‌خواهد.
- Typesense: تقریباً هم‌ارز است؛ Meili به‌خاطر بلوغ بیشتر فیلتر/facet و SDK پایتون انتخاب شد.
