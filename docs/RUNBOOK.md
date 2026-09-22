# RUNBOOK

کارهای عملیاتی روزمره: چه چیزی پیج می‌کند، و برای هرکدام چه کار کنی.
استقرار در [DEPLOY.md](DEPLOY.md) است.


## پرداخت‌ها (فاز ۶)

### فعال کردن یک درگاه

۱. کلید را در env هسته بگذار: `ZARINPAL_MERCHANT_ID` / `IDPAY_API_KEY` / `NEXTPAY_API_KEY`.
۲. ردیفش را در `payment_providers` فعال کن و تنظیمات غیرمحرمانه را بنویس:

```sql
UPDATE payment_providers
   SET is_enabled = true,
       config = '{"sandbox": false, "merchant_env": "ZARINPAL_MERCHANT_ID"}'
 WHERE code = 'zarinpal';
```

۳. مطمئن شو `PUBLIC_API_URL` از اینترنت قابل دسترس است؛ درگاه کاربر را به
   `PUBLIC_API_URL/v1/payments/callback/<provider>` برمی‌گرداند.

### کارت‌به‌کارت

```sql
UPDATE payment_providers
   SET is_enabled = true,
       config = '{"card_number": "6037-xxxx-xxxx-xxxx", "holder_name": "…", "bank": "…"}'
 WHERE code = 'card2card';
```

`PAYMENTS_ADMIN_CHAT_ID` را روی گروه بررسی بگذار و ربات را عضو آن کن. بررسی‌کننده‌ها
باید ردیف فعال در `admin_users` داشته باشند (`tg_id` همان اکانتی که دکمه را می‌زند).
رسیدهای بی‌پاسخ بعد از `settings.payment_review_ttl_hours` (پیش‌فرض ۲۴) منقضی می‌شوند.

### استارز

چیزی برای پیکربندی ندارد جز توکن ربات. برای اشتراک تمدیدشوندهٔ استارز،
`config = '{"subscription_period": 2592000}'` بگذار (تلگرام فقط همین مقدار را می‌پذیرد).

### بازپرداخت

- استارز: `subscriptions.refund(...)` واقعاً `refundStarPayment` را صدا می‌زند.
- درگاه‌های ایرانی و کارت‌به‌کارت: برگشت پول دستی است؛ تابع فقط اشتراک را لغو
  و رویداد را در `audit_log` ثبت می‌کند.

### پرداختی که «گم» شده

```sql
SELECT id, status, provider, provider_ref, amount, created_at
  FROM payments WHERE user_id = :id ORDER BY id DESC LIMIT 10;
```

اگر درگاه پول را گرفته ولی ردیف `pending` مانده، callback نرسیده است: بعد از اطمینان
از تراکنش در پنل درگاه، همان callback را دستی به
`POST /v1/payments/callback/<provider>` بفرست — تسویه ایدمپوتنت است و دوباره فعال‌سازی
نمی‌کند. هرگز `status` را مستقیم در دیتابیس به `paid` تغییر نده؛ این کار اشتراک را
نمی‌سازد و ردی در `audit_log` نمی‌گذارد.

### چرخهٔ عمر

جاب `subscriptions_daily` هر روز ۰۹:۰۰ تهران اجرا می‌شود: یادآوری ۷/۳/۱ روز، انتقال به
مهلت (grace)، سقوط به پلن رایگان و انقضای رسیدهای بررسی‌نشده. اجرای دستی:

```bash
docker compose exec worker arq app.workers.main.WorkerSettings --check
docker compose exec worker python -c "import asyncio; from arq import create_pool; from arq.connections import RedisSettings; from app.config import get_settings; asyncio.run((lambda s: create_pool(RedisSettings.from_dsn(s.redis_url)))(get_settings()).enqueue_job('subscriptions_daily'))"
```


## <a id="api-down"></a>API بالا نمی‌آید

```bash
docker compose -f infra/compose/core.yml ps
docker compose -f infra/compose/core.yml logs --tail=100 api
curl -s http://$WG_CORE_IP:8080/readyz | jq
```

`readyz` می‌گوید کدام وابستگی خراب است:

- `db: false` → Postgres یا PgBouncer. `docker compose logs pgbouncer` و
  `SELECT count(*) FROM pg_stat_activity;` — اگر به سقف اتصال خورده‌ای، PgBouncer را
  ری‌استارت کن، نه Postgres را.
- `redis: false` → ردیس. کش است، ولی rate limit و شمارش پخش روزانه به آن وابسته‌اند.
- `search: false` → Meilisearch. **قطعی نیست**: جستجو خودکار به pg_trgm برمی‌گردد
  (پاسخ `degraded: true` می‌دهد). با آرامش `docker compose restart meilisearch` و بعد
  `python -m app.cli reindex-search`.

## <a id="parser"></a>پارسر شکسته است (نرخ استخراج افتاده)

**نشانه:** صفحهٔ «کرالر» در پنل هشدار «افت ناگهانی استخراج» می‌دهد، یا کانال‌ها کرال
می‌شوند ولی ترک جدیدی اضافه نمی‌شود. یعنی تلگرام HTML پیش‌نمایش را عوض کرده.

۱. تأیید کن که مشکل از پارسر است، نه از یک کانال خاص:

```sql
SELECT day, pages, messages, audio_items,
       round(audio_items::numeric / NULLIF(messages, 0), 3) AS rate
  FROM crawl_stats_daily ORDER BY day DESC LIMIT 7;
```

نرخ سالم حدود ۰٫۶ تا ۰٫۹ است (بسته به کانال‌ها). افتادن به نزدیک صفر در یک روز =
تغییر مارک‌آپ.

۲. یک صفحهٔ واقعی بگیر و با آن fixture بساز:

```bash
curl -s "https://t.me/s/Musicirani_Official" -o indexer/tests/fixtures/preview/new_markup.html
```

۳. تست snapshot را با همان فایل اجرا کن تا دقیقاً ببینی چه چیزی عوض شده:

```bash
cd indexer && pytest tests/test_preview_parser.py -q
```

۴. فقط `webpreview/parser.py` را اصلاح کن (پارسر عمداً ایزوله است؛ هیچ کد دیگری
   به مارک‌آپ وابسته نیست)، fixture جدید را نگه دار و تست را سبز کن.
۵. تا وقتی fix آماده نیست، کرالر را خاموش کن تا cursorها بیهوده جلو نروند:

```sql
UPDATE feature_flags SET value = 'false' WHERE key = 'crawler_enabled';
UPDATE feature_flags SET value = '"mtproto"' WHERE key = 'indexing_source';
```

۶. بعد از fix، کانال‌های آسیب‌دیده را از ابتدا کرال کن (دکمهٔ «از ابتدا» در پنل یا
   بخش «کرال دستی» پایین‌تر). ingest ایدمپوتنت است؛ چیزی تکراری نمی‌شود.

## <a id="resolver"></a>resolver مرده است

**نشانه:** صفحهٔ «کرالر» → کارت resolver می‌گوید «مدار باز است»، یا کاربران روی
ترک‌های تازه خطای «الان قابل پخش نیست» می‌گیرند.

اول مطمئن شو چه چیزی **خراب نیست**: کاتالوگ، سرچ، کتابخانه و پخش هر ترکی که قبلاً
resolve شده سالم‌اند. resolver فقط اولین پخش یک ترک را تأمین می‌کند.

```sql
SELECT resolve_status, count(*) FROM tracks GROUP BY 1;
SELECT id, phone_hint, status, cooling_until, floodwait_24h_s FROM indexer_accounts;
-- آنچه کاربران خواستند و نگرفتند: صف واقعی pre-warm
SELECT id, title, resolve_requests, resolve_attempts, resolve_status
  FROM tracks WHERE resolve_requests > 0 ORDER BY resolve_requests DESC LIMIT 20;
```

معیار سلامت در Grafana: **نرخ resolve درون‌خطی** (سهم پخش‌هایی که مجبور شده‌اند منتظر
MTProto بمانند). اگر بالای ۵٪ ماند، مشکل resolver نیست — pre-warm عقب مانده:
`PREWARM_BATCH` را بالا ببر یا فاصلهٔ کرون را کم کن. آلارم
`InlineResolveRateHigh` همین را می‌گوید.

- `status = 'cooling' / 'limited'` → FloodWait. **هرگز** سرویس را ری‌استارت نکن تا
  «زودتر تلاش کند»؛ جریمه بدتر می‌شود. بگذار سرد شود؛ circuit breaker خودش بعد از
  cooldown دوباره تلاش می‌کند.
- `status = 'banned'` یا session باطل شده → شمارهٔ جدید و لاگین دوباره:

```bash
docker compose run --rm edge python -m tmusic_indexer.login acc1
```

- edge بالا نمی‌آید یا `EDGE_INTERNAL_URL` غلط است → لاگ edge را ببین؛ رویداد
  `resolve.edge_unreachable` در لاگ core یعنی تونل یا آدرس.
- ترک‌هایی که سه بار شکست خورده‌اند `resolve_status = 'failed'` می‌شوند و دیگر
  امتحان نمی‌شوند. بعد از رفع مشکل، برگرداندنشان به صف:

```sql
UPDATE tracks SET resolve_status = 'unresolved', resolve_attempts = 0
 WHERE resolve_status = 'failed';
```

- برای گرم‌کردن دوبارهٔ محبوب‌ها بدون منتظر ماندن کاربر، job `prewarm_resolver` هر
  نیم‌ساعت خودش اجرا می‌شود.

## <a id="recrawl"></a>کرال دستی یک کانال

از پنل: **کرالر → کرال مجدد** (ادامه از cursor) یا **از ابتدا** (پیمایش کامل دوباره).

از API:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN"   "https://api.example.com/admin/crawler/channels/42/recrawl?full=true"
```

از SQL (وقتی پنل در دسترس نیست):

```sql
UPDATE channels
   SET crawl_status = 'idle', crawl_error = NULL, fail_count = 0,
       lease_owner = NULL, lease_until = NULL, next_crawl_at = now(),
       oldest_crawled_msg_id = NULL, newest_crawled_msg_id = NULL   -- فقط برای «از ابتدا»
 WHERE id = :id;
```

worker در tick بعدی (پیش‌فرض ۱۰ ثانیه) آن را برمی‌دارد.

## کانال کرال نمی‌شود

```sql
SELECT id, username, status, crawl_status, crawl_error, fail_count,
       next_crawl_at, extraction_rate
  FROM channels WHERE crawl_status IN ('error', 'preview_disabled')
     OR status = 'failed';
```

| `crawl_status` | معنی | کار |
|---|---|---|
| `preview_disabled` | کانال پیش‌نمایش عمومی ندارد (خصوصی است یا preview را بسته) | از کاربر بخواه بات را ادمین کند (مسیر B)، یا کانال را رد کن |
| `error` + `fail_count < 5` | خطای گذرا؛ backoff نمایی فعال است | کاری لازم نیست |
| `error` + `fail_count >= 5` | کانال کنار گذاشته شده | علت را در `crawl_error` ببین، سپس «کرال مجدد» |

اگر IP کرالر بلاک شده باشد، لاگ edge پر از `crawl.blocked` است. پراکسی اضافه کن:
`CRAWL_PROXIES=http://a:1,http://b:2` و edge را ری‌استارت کن.

## <a id="backups"></a>پشتیبان و بازیابی

بکاپ روزانه ساعت ۰۲:۰۰ و مته (drill) بازیابی یکشنبه‌ها ساعت ۰۴:۰۰ اجرا می‌شود.
هشدار `BackupMissing` یعنی ۳۶ ساعت است بکاپ موفقی نبوده.

```bash
/opt/tmusic/infra/scripts/backup.sh          # اجرای دستی
/opt/tmusic/infra/scripts/restore-test.sh    # مته بازیابی روی دیتابیس یک‌بارمصرف
```

بازیابی واقعی (فقط در فاجعه، با قطعی برنامه‌ریزی‌شده):

```bash
# 1) ترافیک کاربر را ببند
UPDATE feature_flags SET value = 'true' WHERE key = 'maintenance_mode';
# 2) سرویس‌ها را بخوابان (دیتابیس بماند)
docker compose -f infra/compose/core.yml stop api worker bot nginx
# 3) بازگردانی در دیتابیس تازه، نه روی دیتابیس خراب
docker compose exec -T postgres psql -U tmusic -d postgres -c 'CREATE DATABASE tmusic_restored;'
docker compose exec -T postgres pg_restore -U tmusic -d tmusic_restored --no-owner \
  < /var/backups/tmusic/tmusic-<stamp>.dump
# 4) سالم بودن را چک کن، بعد سوییچ کن
docker compose exec -T postgres psql -U tmusic -d tmusic_restored \
  -c 'SELECT count(*) FROM users; SELECT count(*) FROM tracks;'
```

بعد از سوییچ: `python -m app.cli reindex-search` (ایندکس Meilisearch از بکاپ نمی‌آید)
و `maintenance_mode` را خاموش کن.

## حالت تعمیرات

```sql
UPDATE feature_flags SET value = 'true'  WHERE key = 'maintenance_mode';  -- روشن
UPDATE feature_flags SET value = 'false' WHERE key = 'maintenance_mode';  -- خاموش
```

تا یک دقیقه (طول کش فلگ) اثر می‌کند. `/admin/*`، `/healthz`، `/readyz`، `/metrics`،
وبهوک ربات و `/internal` باز می‌مانند؛ بقیه ۵۰۳ می‌گیرند.

## کندی API

۱. Grafana → پنل «API latency»: کدام route؟
۲. اگر همه‌جا کند است، معمولاً دیتابیس است:

```sql
SELECT pid, now() - query_start AS runtime, left(query, 120)
  FROM pg_stat_activity WHERE state = 'active' ORDER BY runtime DESC LIMIT 10;
```

۳. کوئری بلندمدتِ گیرکرده را با `SELECT pg_cancel_backend(:pid);` لغو کن
   (`pg_terminate_backend` فقط وقتی cancel جواب نداد).
۴. اگر `worker_queue_depth` بالا رفته، یک ورکر دیگر اضافه کن:
   `docker compose up -d --scale worker=2`.

## صف بررسی پرداخت‌ها پر شده

هشدار `CardReviewsPilingUp`. در پنل → «پرداخت‌ها» بررسی کن. رسیدهای بی‌پاسخ بعد از
`settings.payment_review_ttl_hours` خودکار منقضی می‌شوند و به کاربر اطلاع می‌دهیم؛
پس صف پر یعنی کاربرهای پرداخت‌کرده منتظرند، نه اینکه سیستم خراب است.

## گزارش کپی‌رایت (SLA ۴۸ ساعت)

پنل → «گزارش‌ها». برای حذف محتوا دکمهٔ «حذف محتوا» ترک/کانال را پنهان یا
blacklist می‌کند و همه‌چیز در `audit_log` ثبت می‌شود. برای حذف کامل یک ترک از
کش‌های edge:

```bash
infra/scripts/purge-track.sh <track_id>
```

## دسترسی به پنل ادمین

- ورود با نام کاربری/رمز (scrypt) یا Telegram Login Widget — هر دو فقط برای ردیف فعال در `admin_users`. رمز فقط از روی سرور و با `app.cli set-admin-password` ست می‌شود؛ API برای آن وجود ندارد.
- افزودن ادمین: پنل → «مدیران»، یا برای اولین owner
  `python -m app.cli add-admin <tg_id>`.
- اگر دسترسی همه قطع شد (مثلاً owner اکانتش را از دست داد)، همان SQL را مستقیم اجرا کن.
- سخت‌گیرانه‌تر: بلوک `allow/deny` در vhost `admin.` را در
  `infra/nginx/core.conf.template` باز کن.

## چرخاندن کلیدها

- **کلید امضای استریم**: کلید جدید را در **ابتدای** `STREAM_SIGNING_KEYS` هر دو طرف
  بگذار (`new,old`)، هر دو را ری‌استارت کن، بعد از انقضای تیکت‌ها (۵ دقیقه) کلید قدیم
  را بردار.
- **JWT**: `gen-keys` بزن؛ همهٔ توکن‌های دسترسی باطل می‌شوند (کاربر با initData
  دوباره لاگین می‌شود، بدون دخالت او).
- **توکن ربات**: بعد از تغییر، حتماً `python -m app.bot.set_webhook` را اجرا کن.
