# ADR-003 — پخش، پلیر و کیفیت متادیتا

وضعیت: **پذیرفته** (۲۰۲۶/۰۹/۲۰ — با هشت تصحیح کاربر که در همین سند اعمال شده‌اند)
تاریخ: ۲۰۲۶/۰۹/۲۰
اثر می‌گذارد روی: [ADR-0004 (استریم پروکسی، بدون ذخیره‌سازی)](adr/0004-streaming-proxy-no-storage.md)،
[ADR-0013 (پروسهٔ edge و استریم MTProto)](adr/0013-edge-process-and-mtproto-streaming.md)،
[ADR-002 (کاتالوگ از پیش‌نمایش وب)](ADR-002-indexing-strategy.md)

---

## ۰. خلاصهٔ یک‌خطی

بخش بزرگی از این بریف **از قبل پیاده شده است** (Range، تیکت امضاشده، کش لبه بدون
توکن، پلیر singleton، MediaSession، صف، shuffle/repeat، تایمر خواب، آفلاین، ادامه
روی دستگاه دیگر). آنچه واقعاً باقی مانده، هفت چیز است که در فازهای ۱۰ تا ۱۳ پایین
آمده‌اند — و **پنج بند بریف با کد یا تصمیم‌های فعلی تداخل دارند** که بخش ۲ آن‌ها را
یکی‌یکی توضیح می‌دهد.

## ۱. وضعیت فعلی هر خواسته

| خواستهٔ بریف | وضعیت | کجا |
|---|---|---|
| توکن کوتاه‌مدت HMAC، bind به `user_id` | ✅ | `shared/tmusic_common/stream_ticket.py`، TTL از `STREAM_TICKET_TTL_S` (الان ۳۰۰ ثانیه) |
| `Accept-Ranges`, `206`, `Content-Range` | ✅ | `indexer/tmusic_indexer/stream.py::stream` |
| `Content-Type` و `Content-Length` دقیق | ✅ | همان‌جا (`_headers`) |
| پاسخ به `HEAD` | ✅ | `Route(..., methods=["GET", "HEAD"])` |
| مسیر Bot API برای فایل ≤۲۰MB | ✅ | `Sources.bot_api` (فقط وقتی خود ربات فایل را دیده — بخش ۲-۲) |
| استریم chunk از MTProto برای فایل بزرگ | ✅ **و مسیر اصلی است** | `Sources.mtproto` (۱MiB chunk) |
| پروکسی روی لینک CDN برای ترک `unresolved` | ❌ **ناممکن** (ADR-002 §۸) — پیامدش در همان‌جا | — |
| قرارداد یکسان برای فرانت | ✅ | فرانت فقط `{url, size, mime, expires_at}` می‌بیند |
| کش لبه با کلید بدون توکن | ✅ | `infra/nginx/snippets/stream-cache.conf` (`proxy_cache_key "s:$track_id:$slice_range"`) |
| Prefetch ترک بعدی | ❌ | فاز ۱۰ — با یک تداخل (بخش ۲-۳) |
| timeout و عدم قفل‌شدن worker | ✅ | استریم async و chunked؛ `nginx slice` |
| متریک TTFB / underrun / نرخ خطا / **نرخ resolve درون‌خطی** | ⚠️ نیمه | `edge_stream_bytes_total`, `edge_stream_errors_total` هست؛ آن چهار نیست → فاز ۱۰ |
| `<audio>` singleton خارج از روتر | ✅ | `player/engine.ts` — اصلاً داخل درخت React نیست |
| state در Zustand (صف، shuffle، repeat، volume، سرعت، منبع) | ✅ | `store/player.ts` |
| MediaSession کامل + هندلرها | ✅ (seekto خالی) | `engine.ts::setMediaSession` |
| افزودن به صف / پخش بعدی / حذف / جابه‌جایی | ✅ منطق، ⚠️ UI drag | `store/player.ts`، `FullPlayer::QueueSheet` |
| تفکیک «صف دستی» از «ادامهٔ منبع» | ❌ | فاز ۱۱ |
| shuffle با حفظ ترتیب اصلی | ❌ (ترتیب اصلی گم می‌شود) | بخش ۲-۴ |
| repeat off/all/one | ✅ | `cycleRepeat` |
| Autoplay رادیو در پایان صف | ❌ (الان فقط می‌ایستد) | فاز ۱۱ |
| پخش خوش‌بینانه + اسپینر | ✅ | `isLoading` قبل از اولین بایت |
| گذار نرم (fade) | ❌ | فاز ۱۱ |
| ادامه از جای قطع‌شده روی دستگاه دیگر | ✅ (هر ۱۵ ثانیه) | `App.tsx` + `PUT/GET /v1/me/playback` |
| retry با backoff و پیام روشن | ⚠️ نیمه | خطا دسته‌بندی می‌شود (`plan_limit`/`unavailable`/`network`) ولی retry خودکار ندارد |
| مینی‌پلیر شیشه‌ای + نوار پیشرفت + swipe | ⚠️ نیمه | `MiniPlayer.tsx` هست؛ swipe افقی ندارد |
| ترنزیشن shared element کاور | ❌ | فاز ۱۲ |
| پلیر کامل: کاور بزرگ، گرادیان از رنگ کاور | ✅ | `FullPlayer.tsx` + `lib/color.ts` |
| **چیپ کانال مبدأ با لینک عضویت** | ❌ **و API هم آن را نمی‌دهد** | بخش ۲-۵ |
| seek با زمان گذشته و **باقی‌مانده** | ✅ (از قبل درست بود — ردیف قبلی این جدول اشتباه بود) | `FullPlayer.tsx::SeekBar` |
| ردیف پایین: صف، تایمر خواب، اشتراک‌گذاری | ⚠️ صف ✅ خواب ✅ اشتراک ❌ | فاز ۱۲ |
| لایک در متادیتا، swipe پایین برای بستن | ✅ | `FullPlayer.tsx` |
| منوی ترک: play next, queue, like, playlist, artist | ✅ | `TrackActions.tsx` |
| منوی ترک: album, join channel, share, report, similar | ❌ | فاز ۱۲ |
| Download / ارسال به چت | ✅ | `TrackActions` + `/internal/send` |
| نرمال‌سازی: حذف پسوند، `@username`، ایموجی | ✅ | `app/domain/text/tag_stripper.py` |
| استخراج خواننده از الگوها | ✅ | `app/domain/text/artist_parser.py` |
| اولویت با `performer` تلگرام | ✅ | `services/ingest.py` |
| جدول alias خواننده (فا/انگ/فینگلیش) | ✅ | `artist_aliases` + ADR-0014 |
| fallback به نام کانال به‌جای Unknown | ✅ | `lib/format.ts::artistNames` |
| صف بازبینی متادیتا در پنل + اصلاح گروهی | ✅ | `admin/src/screens/Metadata.tsx` |
| ≥۵۰ نمونهٔ واقعی فارسی در تست پارسر | ✅ ۶۱ نمونه در `tests/fixtures/persian_titles.py` | بخش ۷ |
| کاور از thumbnail تلگرام | ✅ | `has_thumb` + `/t/{id}` روی edge |
| کاور از تگ ID3 → **S3** | ❌ **و با ADR-0004 تداخل دارد** | بخش ۲-۶ |
| کاور تولیدی از hash نام خواننده | ❌ | فاز ۱۲ |
| رنگ غالب cache‌شده در دیتابیس | ⚠️ (الان سمت کلاینت، cache حافظه‌ای) | فاز ۱۲ |

---

## ۲. تداخل‌ها با کد و تصمیم‌های فعلی

### ۲-۱. `GET /api/stream/:trackId` — بایت‌ها از هستهٔ ایران رد نمی‌شوند

بریف یک endpoint روی API می‌خواهد. معماری فعلی (ADR-0004 و §۳ سند معماری) عمداً
این‌طور نیست:

- کلاینت `POST /v1/tracks/{id}/stream` می‌زند و یک **تیکت امضاشده** می‌گیرد؛
- بایت‌ها را از `https://cdn.<domain>/s/{track_id}?t=<ticket>` می‌گیرد که روی
  **لبهٔ خارج از ایران** است.

اگر بایت‌ها از `api.<domain>` رد شوند، کل ترافیک صوت باید از ایران عبور کند: هم
هزینهٔ پهنای باند چند برابر می‌شود (بخش ۱۰ معماری: کل بودجه ۵۰–۲۰۰ دلار)، هم تأخیر
اضافه می‌شود، هم کش ۴۰ گیگابایتی لبه بی‌فایده می‌شود.

**تصمیم (تأییدشده):** قرارداد فعلی می‌ماند و `GET /api/stream/:trackId` یک قرارداد
*منطقی* است، نه یک مسیر روی API: کلاینت تیکت می‌گیرد و بایت‌ها را از لبه می‌خواند.
این در `API.md` صریح نوشته شد، و یک **تست نگهبان در CI** اضافه شد که اگر روزی کسی
بایت صوت را از API هسته سرو کند، build می‌شکند:
`backend/tests/unit/test_no_bytes_through_core.py`.

### ۲-۲. «ترک resolved → `file_id` و `getFile`» فقط برای فایل‌هایی کار می‌کند که ربات دیده

`getFile` با `file_id` **Bot API** کار می‌کند. `file_id`ای که resolver از MTProto
می‌گیرد از جنس دیگری است و Bot API آن را نمی‌شناسد. بنابراین:

- ترک‌هایی که از `channel_post` آمده‌اند (بات ادمین کانال) `bot_file_id` دارند →
  مسیر Bot API، سقف ۲۰MB. ✅ پیاده است.
- ترک‌هایی که کرالر پیدا کرده و resolver حل کرده **`file_id` بات ندارند** → مسیر
  MTProto. این مسیر اصلی است و سقف حجمی ندارد.

یعنی ترتیب اولویت بریف (اول getFile، آخر MTProto) در عمل برعکس است: انتخاب بر اساس
«ربات این فایل را دیده؟» است، نه «resolve شده؟».

**تصمیم (تأییدشده):** ترتیب در کد و در `ARCHITECTURE.md` تصحیح شد، و ستون
`tracks.file_id` به **`bot_file_id`** تغییر نام داد (مهاجرت ۰۰۰۶) تا این اشتباه
دوباره تکرار نشود؛ `file_unique_id` همان هویت پایدار مشترک هر دو مسیر می‌ماند.

### ۲-۳. Prefetch ترک بعدی، سقف پخش روزانه را می‌سوزاند

`stream.issue_ticket` هنگام صدور تیکت `_count_play` را صدا می‌زند و ترک را در
`plays:{user}:{yyyymmdd}` می‌گذارد؛ کاربر رایگان سقف روزانه دارد. اگر فرانت برای
prefetch تیکت ترک بعدی را بگیرد، ترکی که شاید هرگز پخش نشود از سهمیهٔ کاربر کم
می‌شود.

**تصمیم (تأییدشده):** `?prefetch=1` تیکتی می‌دهد که نمی‌شمارد، و برای اینکه قابل
سوءاستفاده نباشد **سقف بایت داخل خود تیکت امضا می‌شود** (`max_bytes`، پیش‌فرض
۵۱۲KB). لبه بیشتر از آن را سرو نمی‌کند. با شروع پخش واقعی، کلاینت تیکت کامل و
شمارش‌شونده می‌گیرد — یعنی prefetch به پخش «ارتقا» پیدا می‌کند، نه اینکه جایش را
بگیرد.

### ۲-۴. shuffle فعلی ترتیب اصلی را دور می‌ریزد

`store/player.ts::shuffled()` آرایهٔ جدید می‌سازد و جایگزین `queue` می‌کند؛ با
خاموش‌کردن shuffle ترتیب قبلی برنمی‌گردد. این دقیقاً چیزی است که بریف نمی‌خواهد.
اصلاح کوچک است (نگه‌داشتن `originalQueue` یا آرایهٔ `order`) ولی روی تست‌های موجود
پلیر اثر دارد.

### ۲-۵. چیپ کانال مبدأ: API اصلاً این داده را نمی‌دهد

`TrackOut` فقط `channels_count` دارد. برای چیپ «از کانال @X» با لینک عضویت، باید
کانال مبدأ (id، username، title) در پاسخ بیاید. رابطه در دیتابیس هست
(`channel_tracks`)، ولی یک ترک ممکن است در چند کانال باشد.

**قاعدهٔ انتخاب (تأییدشده)** — هدف این چیپ جذب عضو است، پس:

۱. کانالی که **کاربر عضوش نیست** (نشان‌دادن کانالی که کاربر دارد، فضای تبلیغی را
   هدر می‌دهد)؛
۲. بین آن‌ها: `is_featured`، بعد پرمخاطب‌ترین (`subscribers_count`)؛
۳. قدیمی‌ترین انتشار فقط به‌عنوان آخرین تای‌بریکر.

`TrackOut` یک آبجکت کانال می‌گیرد (id، username، title، عضو هست یا نه)، نه فقط
`channels_count`. فاز ۱۲، اولویت اول.

### ۲-۶. ذخیرهٔ کاور در S3: یک وابستگی که لازم نیست

- thumbnail تلگرام برای اکثر ترک‌ها همین حالا کار می‌کند،
- S3 یک سرویس، یک کلید، یک خط هزینه و یک مسیر پاک‌سازی جدید اضافه می‌کند،
- و همان بایت‌ها را می‌شود از مسیری سرو کرد که از قبل کش و اعتبارسنجی دارد.

**تصمیم (تأییدشده، با دلیل تصحیح‌شده):** کاور ID3 از همان مسیر لبه سرو می‌شود
(`GET /t/{id}` در نبود thumbnail به APIC داخل فایل برمی‌گردد) و اگر هیچ‌کدام نبود،
کاور تولیدی از hash نام خواننده سمت کلاینت.

دلیل این انتخاب **سادگی عملیاتی و حذف یک وابستگی (S3)** است. دلیل حقوقی نیست و
نباید باشد: کش nginx هم ذخیره‌سازی است و مرز «ما میزبانی نمی‌کنیم» را جابه‌جا
نمی‌کند — تفاوت واقعی این است که کاور یک تصویر بندانگشتی است، نه خود اثر صوتی، و
ریسکش با توزیع فایل موسیقی قابل مقایسه نیست.

### ۲-۷. دو معیار پذیرش که بازنویسی شدند (تأییدشده)

- «خروج از مینی‌اپ و بازگشت، پخش را قطع نکند»: بستن مینی‌اپ در تلگرام WebView را
  نابود می‌کند و صدا قطع می‌شود؛ هیچ API ای این را تغییر نمی‌دهد. آنچه شدنی است و
  پیاده می‌شود: (الف) جابه‌جایی بین صفحات داخل اپ پخش را قطع نمی‌کند (الان هم
  نمی‌کند)، (ب) با بازگشت، از همان ثانیه ادامه می‌دهد (`/v1/me/playback`).
- «کنترل از نوار اعلان و صفحهٔ قفل»: روی اندروید با MediaSession کار می‌کند؛ روی
  iOS داخل WebView تلگرام محدود و غیرقابل تضمین است.

صورت نهایی معیارها:

- «**موقعیت پخش و صف حفظ شود و با بازگشت، از همان ثانیه ادامه دهد**» (به‌جای «پخش
  قطع نشود»).
- «**کنترل نوار اعلان روی اندروید الزامی است**؛ iOS داخل WebView تلگرام best effort
  و خارج از معیار پذیرش.»

---

## ۳. فازبندی (ادامهٔ فازبندی موجود)

فازهای ۰ تا ۹ محصول و فازهای ۰ تا ۶ ADR-002 تمام شده‌اند. این چهار فاز بعدی‌اند.

| فاز | کار | معیار «تمام» |
|---|---|---|
| **۱۰ ✅ — زیرساخت استریم** | **چهار** متریک (TTFB، buffer underrun، نرخ خطای پخش، و **نرخ resolve درون‌خطی**)؛ تیکت `prefetch` با سقف بایت امضاشده؛ رفتار صریح خرابی resolver (پیام روشن، صف با اولویت بالا، پلیر قفل نشود)؛ pre-warm به مسیر بحرانی (ترند، لایک‌شده، پلی‌لیست، کانال‌های featured)؛ تصحیح ترتیب منبع + تغییر نام `bot_file_id`؛ retry با backoff در فرانت؛ تست نگهبان CI و مستندسازی در `API.md` | داشبورد هر چهار متریک را نشان دهد؛ تیکت prefetch سقف روزانه را کم نکند و بیشتر از سقفش بایت ندهد؛ با resolver خاموش، پلیر پیام می‌دهد و قفل نمی‌شود |
| **۱۱ ✅ — موتور پخش** | صف دستی جدا از ادامهٔ منبع؛ shuffle با حفظ ترتیب؛ autoplay رادیو در پایان صف؛ fade کوتاه ابتدا/انتها؛ prefetch چند صد کیلوبایت ترک بعدی؛ sync ۱۰ ثانیه‌ای | تست‌های store: خاموش‌کردن shuffle ترتیب اصلی را برگرداند؛ صف دستی قبل از ادامهٔ خودکار پخش شود |
| **۱۲ ✅ — UI پلیر و منوی ترک** | چیپ کانال مبدأ + لینک عضویت (با فیلد جدید در `TrackOut`)؛ زمان باقی‌مانده؛ اشتراک‌گذاری در ردیف پایین؛ swipe افقی مینی‌پلیر؛ shared element کاور؛ منوی ترک: album، join channel، share، report، similar؛ صف مجازی‌سازی‌شده؛ کاور ID3 از لبه + کاور تولیدی؛ رنگ غالب cache‌شده | تست‌های کامپوننت برای هر آیتم منو؛ صف ۵۰۰تایی بدون افت فریم |
| **۱۳ ✅ — کیفیت متادیتا** | **اول**: مجموعهٔ ≥۵۰ نمونهٔ واقعی فارسی و سنجش پارسر روی آن (خط پایه)؛ **بعد**: fallback نام کانال به‌جای Unknown و بهبود پارسر؛ **بعد**: سنجش دوباره و گزارش دلتا؛ صف بازبینی متادیتا در پنل با اصلاح تکی و گروهی | گزارش دقت قبل/بعد روی همان ۵۰ نمونه؛ اصلاح گروهی در پنل کار کند و audit شود |

### وابستگی‌ها

- فاز ۱۲ به یک تغییر کوچک API نیاز دارد (کانال مبدأ در `TrackOut`) → قبل از UI.
- فاز ۱۱ (prefetch) به فاز ۱۰ (تیکت بدون شمارش) وابسته است، وگرنه سهمیهٔ کاربر
  می‌سوزد.
- فاز ۱۳ مستقل است و می‌تواند موازی پیش برود.

## ۴. آنچه در فاز ۱۰ ساخته شد

| کار | کجا | تست |
|---|---|---|
| تیکت prefetch بدون شمارش، با سقف بایت امضاشده | `services/stream.py::issue_ticket(prefetch=…)` + `StreamTicket.max_bytes` | `test_playback.py::test_a_prefetch_ticket_does_not_spend_the_daily_limit` / `::test_the_edge_refuses_to_serve_past_a_prefetch_ceiling` |
| اجرای سقف روی لبه | `indexer/.../stream.py::stream` | همان بالا (۲۰۶ بریده‌شده و ۴۱۶) |
| رفتار خرابی resolver: پیام روشن + ثبت تقاضا + عدم قفل | `issue_ticket` → `Unavailable(reason="resolving")` + `resolving.record_demand` | `test_playback.py::test_a_resolver_outage_answers_clearly_and_records_the_demand` |
| pre-warm روی مسیر بحرانی (تقاضا، لایک، پلی‌لیست، featured) هر ۱۰ دقیقه | `resolving._PREWARM_SQL` + کرون | `test_playback.py::test_prewarm_goes_after_what_people_actually_asked_for` / `::test_featured_channels_and_playlists_are_warmed_before_the_rest` |
| متریک ۱ — زمان تا اولین صدا | کلاینت → `POST /v1/telemetry/playback` → `playback_start_seconds` | `test_playback.py::test_the_client_can_report_what_only_it_can_measure` |
| متریک ۲ — buffer underrun | رویداد `waiting` در پلیر → `playback_underruns_total` | `player.test.ts::reports time to first sound, stalls and failures` |
| متریک ۳ — نرخ خطای پخش | `playback_errors_total{kind}` | همان بالا |
| متریک ۴ — **نرخ resolve درون‌خطی** | `playback_inline_resolve_total` ÷ `playback_tickets_total{kind="play"}` | `test_playback.py::test_a_resolver_outage_…` |
| داشبورد و آلارم هر چهار متریک | `infra/monitoring/*` (`InlineResolveRateHigh`, `PlaybackStartSlow`, `PlaybackFailing`) | — |
| retry با backoff فقط برای خطای شبکه | `store/player.ts::load` | `player.test.ts::retries a network failure…` / `::does not retry a plan limit` |
| تصحیح ترتیب منبع + تغییر نام `bot_file_id` | مهاجرت ۰۰۰۶، `stream.pick_source`، `ARCHITECTURE.md §۶-۱` | کل سوییت پخش |
| تست نگهبان CI برای «بایت از هسته عبور نکند» | `tests/unit/test_no_bytes_through_core.py` | خودش (با تزریق یک نقض، قرمز می‌شود) |

## ۵. آنچه در فاز ۱۱ ساخته شد

| کار | کجا | تست |
|---|---|---|
| دو صف جدا: `queue` (منبع) و `manual` (صف دستی کاربر) | `store/player.ts` | `player.test.ts::plays the manual queue before the source continues` |
| بازگشت از ترک دستی به همان‌جای منبع | `previous()` | `::goes back from a manual track to the source track it interrupted` |
| `upcoming()` به‌عنوان تنها منبع حقیقت صف برای UI | `store/player.ts` + `FullPlayer::QueueSheet` | `::edits the queue without losing the current track` |
| جابه‌جایی صف دستی با drag | `Reorder` در `QueueSheet` | (تایپ‌چک + رندر) |
| shuffle با حفظ ترتیب اصلی (`unshuffled`) | `setShuffle` | `::restores the original order when shuffle is turned off` |
| autoplay رادیو در پایان صف (و توقف تمیز وقتی خاموش است یا ایستگاهی نیست) | `next()` + `radioFor` | `::keeps playing with a station when the queue runs out` / `::stops cleanly when autoplay is off` |
| fade کوتاه ورود/خروج (نه crossfade — یک المان `<audio>` دو منبع را هم‌پوشان نمی‌کند) | `engine.ts::fadeTo` | `::fades in instead of starting at full volume` |
| prefetch ترک بعدیِ **واقعی** (با احتساب صف دستی) | `attach()` → `prefetch(upcoming()[0])` | `::prefetches whatever actually plays next` |
| `volume` در state و هدف fade | `setVolume` | همان تست fade |
| seek از صفحهٔ قفل (`seekto`) | `setMediaSession` | — |
| sync موقعیت هر ۱۰ ثانیه | `App.tsx` | — |

صف دستی عمداً روی دستگاه sync نمی‌شود: قرارداد `/v1/me/playback` فقط صف منبع و
موقعیت را نگه می‌دارد، و «چیزی که همین حالا دستی صف کردم» حالِ همین دستگاه است.

## ۶. آنچه در فاز ۱۲ ساخته شد

| کار | کجا | تست |
|---|---|---|
| **چیپ کانال مبدأ با لینک عضویت**، با قاعدهٔ «عضو نیست → featured → پرمخاطب → قدیمی‌ترین» | `library.attribution` + `TrackOut.channel` + `FullPlayer::ChannelChip` | `test_playback.py::test_a_track_is_credited_to_the_channel_worth_joining` و `::test_attribution_prefers_an_unjoined_channel_over_a_featured_one` / `player-ui.test.tsx::channel attribution` |
| منوی ترک: آلبوم، عضویت در کانال، اشتراک‌گذاری، گزارش، ترک‌های مشابه داخل همان شیت | `TrackActions.tsx` | `player-ui.test.tsx::the track menu` (۵ تست) |
| گزارش کاربر (متادیتای غلط / کپی‌رایت / نامناسب / خراب) با SLA متفاوت | `services/reports.py` + `POST /v1/tracks/{id}/report` | `test_playback.py::test_a_listener_can_report_a_track` |
| لینک اشتراک‌گذاری `startapp=tr_<id>` و صفحهٔ ترک برای بازکردنش | `TrackActions::shareLink` + `Detail::TrackScreen` + روت `/track/:id` | `player-ui.test.tsx::shares a deep link…` |
| swipe افقی مینی‌پلیر (بعدی/قبلی) و swipe بالا برای پلیر کامل | `MiniPlayer.tsx` | (تایپ‌چک؛ رفتار در store تست شده) |
| ترنزیشن shared element کاور | `layoutId="cover"` در مینی‌پلیر و پلیر کامل | — (از قبل بود) |
| صف مجازی‌سازی‌شده برای ۵۰۰ ترک | `QUEUE_CHUNK` + `content-visibility` در `QueueSheet` | `player-ui.test.tsx::does not put five hundred rows in the DOM at once` |
| کاور از تگ ID3 وقتی تلگرام thumbnail ندارد (بدون ذخیره‌سازی) | `id3.parse_artwork` + `Sources.thumbnail` | `test_id3.py` (۴ تست) + `test_stream.py::falls back to its id3 cover` |
| کاور تولیدی از seed | `Cover` (از قبل بود) | — |
| رنگ غالب کاور، cache‌شده در دیتابیس | مهاجرت ۰۰۰۷ + `PUT /v1/tracks/{id}/palette` + `App.tsx` | `test_playback.py::test_the_first_client_to_render_a_cover_teaches_everyone_else` / `lib.test.ts::stored palettes` |

رنگ کاور را کلاینت می‌فرستد، چون پیکسل‌ها فقط آنجا هستند (هسته هیچ بایتی از مدیا
نمی‌بیند). فقط **یک بار** نوشته می‌شود و شکلش سخت‌گیرانه اعتبارسنجی می‌شود؛ بدترین
حالتِ سوءاستفاده یک گرادیان زشت است، نه چیزی بیشتر.

## ۷. فاز ۱۳ — اول سنجش، بعد اصلاح، بعد سنجش دوباره

مجموعهٔ سنجش: **۶۱ عنوان واقعی** در `backend/tests/fixtures/persian_titles.py`، به
شکل‌هایی که کانال‌های فارسی واقعاً پست می‌کنند — نام فایل خام، برندینگ کانال چسبیده به
عنوان، نویز کیفیت، ترتیب برعکس، featureها، خط تیرهٔ تزئینی، و چند نمونه که عمداً
**هیچ خواننده‌ای ندارند** تا مطمئن شویم پارسر چیزی از خودش نمی‌سازد.

```bash
cd backend && pytest tests/unit/test_title_corpus.py -s -q
```

| سنجش | خواننده | عنوان | هر دو |
|---|---|---|---|
| **خط پایه** (قبل از فاز ۱۳) | ۶۷٪ | ۶۲٪ | ۵۹٪ |
| **بعد از اصلاح پارسر** | **۱۰۰٪** | **۱۰۰٪** | **۱۰۰٪** |

### چه چیزی عوض شد (هر کدام از یک الگوی شکست در همان گزارش آمد)

| الگو | اصلاح |
|---|---|
| `Googoosh_Pol_320.mp3`, `hayedeh-gole-sangam.mp3` | عنوانی که در واقع نام فایل است: پسوند حذف، اولین `_`/`-` جداکنندهٔ خواننده/عنوان |
| `01 - Dariush - ...`, `03. Ebi - ...` | شمارهٔ ترک ابتدای عنوان دیگر خواننده حساب نمی‌شود |
| `New | Reza Sadeghi - ...`, `New Song 2026 | ...` | پیشوندهای انتشار و سال تنها حذف می‌شوند |
| `Sogand ft. Sirvan Khosravi - Havaye To` | `ft.` سمت خواننده تا جداکننده متوقف می‌شود، نه تا آخر رشته |
| `محسن چاوشی (با حضور سینا سرلک)` | «با حضور» مثل feat شناخته می‌شود |
| `Nafas (Moein)`, `دیوار [امیر تتلو]` | خوانندهٔ داخل پرانتز — **فقط** وقتی نامی است که کاتالوگ می‌شناسد |
| `Ebi - Khalij` با performer=`Telegram` | فهرست performerهای بی‌معنی گسترده شد (telegram، music، کانال، …) |
| `معین ـ شب بارونی`, `★ Moein ★ Nafas ★` | جداکنندهٔ «کشیده‌شده» قبل از حذف تزئینات به جداکنندهٔ واقعی تبدیل می‌شود |
| `آهنگ جدید` | وقتی حذف پیشوند فقط یک کلمهٔ عمومی باقی بگذارد، عنوان اصلی می‌ماند |
| `... با کیفیت 320`, `بسیار زیبای معین` | واژه‌های تعریفی بین «آهنگ» و نام خواننده حذف می‌شوند |

دو چیز که تست‌های موجود جلویشان را گرفتند و درست بود که گرفتند: `(Remix)` **نباید**
حذف شود (ریمیکس یک ضبط دیگر است و حذف برچسب، dedup را به اشتباه می‌اندازد)، و
`Track` به‌تنهایی یک عنوان قابل قبول است.

### حلقهٔ اصلاح

- ترکی که پارسر مطمئن نبوده (`metadata_confidence < 60`) یا کاربری گزارش کرده، در
  **صف بازبینی پنل** می‌آید (گزارش‌ها اول).
- اصلاح تکی، یا **اعمال روی همهٔ ترک‌های همان خوانندهٔ غلط** — چون کانال‌ها یک نام
  مچاله‌شده را صدها بار پست می‌کنند.
- هر اصلاح گزارش‌های باز آن ترک را می‌بندد و در `audit_log` ثبت می‌شود.
- در مینی‌اپ، ترکی که خواننده ندارد به‌جای «Unknown artist» نام کانال مبدأ را نشان
  می‌دهد.
