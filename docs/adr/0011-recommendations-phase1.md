# ADR-0011: پیشنهاددهی فاز اول = item-based CF + هم‌رخدادی در پلی‌لیست

- وضعیت: پذیرفته

## تصمیم
- job شبانه: ماتریس کاربر×ترک از `play_history` (فقط پخش‌های `completed`) + لایک‌ها + هم‌رخدادی در `playlist_tracks`؛ شباهت کسینوسی؛ ۵۰ همسایهٔ برتر هر ترک در `track_similarity`.
- Discover Weekly (دوشنبه‌ها): همسایه‌های ترک‌های اخیر کاربر، منهای آنچه شنیده، با تنوع خواننده (حداکثر ۲ ترک از هر خواننده).
- Trending: پنجره‌های ۲۴ ساعت / ۷ روز / ۳۰ روز از پخش‌ها؛ Most Added از `channels_count`.
- Cold start: Trending + Featured.
- اجرا با numpy/scipy sparse در worker؛ در مقیاس فعلی (کمتر از ۱M ترک) در چند دقیقه تمام می‌شود.

## فاز دو
content-based (خواننده/ژانر/BPM) و embedding ها — همراه ADR-0012.
