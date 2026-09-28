# API.md

قرارداد کامل و ماشین‌خوان در [`openapi.json`](openapi.json) است. این فایل از خود اپ تولید می‌شود، پس دستی ویرایشش نکن. در محیط dev، مستندات تعاملی در `http://localhost:8000/docs` در دسترس است (در prod خاموش است).

برای به‌روز کردن `openapi.json`:

```bash
cd backend && python -c "import json; from app.api.main import create_app; open('../docs/openapi.json','w',encoding='utf-8').write(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))"
```

در فاز ۴ همین فایل با `openapi-typescript` به تایپ‌های TypeScript مینی‌اپ تبدیل می‌شود.

## قراردادهای عمومی

| موضوع | قرارداد |
|---|---|
| احراز هویت | `Authorization: Bearer <access_token>` (JWT با الگوریتم EdDSA و عمر ۱۵ دقیقه) |
| خطا | `{"error": {"code": "...", "message": "...", "details": {...}}}`. کلاینت متن نمایشی را از روی `code` ترجمه می‌کند |
| صفحه‌بندی لیست‌ها | `?cursor=&limit=` → `{"items": [...], "next_cursor": "..."}`. کرسر opaque است |
| صفحه‌بندی سرچ | `offset/limit` (رتبه‌بندی relevance است، سقف ۱۰۰۰ نتیجه) |
| ردیابی | `X-Request-ID` برگردانده می‌شود. اگر کلاینت مقدار معتبری بفرستد، همان حفظ می‌شود |
| rate limit | به‌ازای کاربر (پیش‌فرض ۲۴۰ در دقیقه) و به‌ازای IP در مسیرهای auth. پاسخ `429` با هدر `Retry-After` |

| کد خطا | HTTP | معنی |
|---|---|---|
| `unauthorized` | 401 | توکن یا initData نامعتبر |
| `forbidden` | 403 | بن، کانال مسدود، یا توکن impersonation روی مسیر نوشتنی |
| `not_found` | 404 | منبع پیدا نشد |
| `plan_limit` | 402 | سقف پلن پر شده. `details.kind` یکی از `channels` یا `daily_plays` است و `details.limit` مقدار سقف |
| `invalid_input` | 422 | ورودی نامعتبر. برای افزودن کانال، `details.reason` یکی از `private_link`، `not_telegram` یا `invalid` است |
| `rate_limited` | 429 | بیش از حد مجاز درخواست |
| `unavailable` | 503 | منبع پخش یا edge در دسترس نیست. `details.reason`: `no_source` یا `resolving` (ترک هنوز resolve نشده و resolver در دسترس نیست — [ADR-002](ADR-002-indexing-strategy.md)) |

## Endpoint ها (فاز ۱ تا ۳)

| متد | مسیر | توضیح |
|---|---|---|
| POST | `/v1/auth/telegram` | `{init_data}` → توکن‌ها + `me` + `start_param` |
| POST | `/v1/auth/refresh` | چرخش refresh token. استفادهٔ مجدد از توکن مصرف‌شده، کل family را باطل می‌کند |
| POST | `/v1/auth/logout` | باطل‌کردن family |
| GET | `/v1/me` | پروفایل، پلن، سقف‌ها و قابلیت‌ها |
| PATCH | `/v1/me/lang` | `{lang: fa\|en}` |
| GET / POST | `/v1/library/channels` | کانال‌های کاربر / افزودن با `{ref}` (یوزرنیم یا لینک) |
| DELETE | `/v1/library/channels/{id}` | حذف از کتابخانه (خود کانال سراسری می‌ماند) |
| GET | `/v1/channels/categories` | دسته‌بندی‌ها |
| GET | `/v1/channels/featured` | `?category_id` + کرسر |
| GET | `/v1/channels/{id}` | جزئیات کانال و وضعیت ایندکس (`status`، `progress_pct`) |
| GET | `/v1/channels/{id}/tracks` | ترک‌های یک کانال |
| GET | `/v1/library/tracks` | فیلترها: `channel_id`، `artist_id`، `album`، `language`، `year`، `min_duration`، `max_duration` |
| GET | `/v1/library/artists` | خوانندگان کتابخانه، به ترتیب تعداد ترک |
| GET | `/v1/library/albums` | آلبوم‌ها (فقط ترک‌هایی که تگ آلبوم دارند) |
| GET | `/v1/tracks/{id}` | اگر id یک ترک تکراری باشد، نسخهٔ canonical برگردانده می‌شود |
| GET | `/v1/artists/{id}` و `/v1/artists/{id}/tracks` | صفحهٔ خواننده |
| GET | `/v1/search` | `q`، `scope=library\|global`، همان فیلترها، `offset/limit`. `degraded=true` یعنی پاسخ از fallback پستگرس آمده |
| GET | `/v1/search/suggest` | تاریخچهٔ منطبق + حداکثر ۶ ترک |
| DELETE | `/v1/search/history` | پاک‌کردن تاریخچهٔ سرچ |
| POST | `/v1/tracks/{id}/stream` | `{url, thumb_url, expires_at, size, mime}`. URL امضاشده روی edge است و ۵ دقیقه اعتبار دارد |
| POST | `/v1/channels/suggest` | `{ref}` → اگر کانال ایندکس شده باشد همان‌جا به کتابخانه اضافه می‌شود (`status=subscribed`)، وگرنه درخواست در صف کاندیدها ثبت می‌شود (`status=queued`) — [ADR-002 §۳](ADR-002-indexing-strategy.md) |

### پخش

`GET /api/stream/:trackId` یک **قرارداد منطقی** است، نه یک مسیر روی این API. بایت‌ها
هرگز از هستهٔ ایران عبور نمی‌کنند (ADR-0004): کلاینت یک تیکت امضاشده می‌گیرد و
بایت‌ها را مستقیم از لبهٔ خارج می‌خواند. تستِ نگهبان
`backend/tests/unit/test_no_bytes_through_core.py` اگر روزی کسی بایت صوت را از API
سرو کند، CI را می‌شکند.

```
POST /v1/tracks/42/stream
→ {"url": "https://cdn.example.com/s/42?t=<ticket>", "expires_at": 1790000000, ...}

GET https://cdn.example.com/s/42?t=<ticket>   (Range: bytes=0-)
→ 206 Partial Content, Accept-Ranges: bytes
```

- تیکت HMAC است، به `user_id` bind شده و TTL کوتاه دارد؛ لینک بیرون از اپ کار نمی‌کند.
- `HEAD` هم پشتیبانی می‌شود (بعضی مرورگرها اول HEAD می‌زنند).
- **`?prefetch=1`**: تیکتی برای گرم‌کردن ترک بعدی. سقف پخش روزانه را مصرف نمی‌کند و
  سقف بایتش داخل امضا است (پیش‌فرض ۵۱۲KB)؛ لبه بیشتر از آن سرو نمی‌کند. با شروع پخش
  واقعی، کلاینت دوباره بدون این پارامتر تیکت می‌گیرد.
- اگر ترک هنوز resolve نشده و resolver در دسترس نباشد، پاسخ `503` با
  `details.reason = "resolving"` است — تقاضا ثبت می‌شود و pre-warm سراغش می‌رود.
  کلاینت باید پیام بدهد، نه اینکه قفل شود.

### تله‌متری پخش

```
POST /v1/telemetry/playback
{"kind": "start", "ms": 780}            ← از لمس تا اولین صدا
{"kind": "underrun"}                     ← وقفهٔ بافر وسط پخش
{"kind": "error", "reason": "network"}   ← شکست پخش سمت کلاینت
→ 204
```

چیزی در دیتابیس نوشته نمی‌شود؛ فقط شمارندهٔ Prometheus. این دو عدد را فقط کلاینت
می‌بیند و بدون آن‌ها «نرخ buffer underrun» و «نرخ خطای پخش» قابل اندازه‌گیری نیست.

- مینی‌اپ URL را مستقیم به `<audio>` می‌دهد و قبل از `expires_at` یک ticket تازه می‌گیرد. فراخوانی مجدد برای همان ترک، سقف روزانه را دوباره مصرف نمی‌کند.
- `id` داخل URL همیشه id نسخهٔ canonical است، پس کش edge بین همهٔ کاربران مشترک است.

## API داخلی (core ↔ edge)

این مسیرها فقط روی WireGuard در دسترس‌اند و `Authorization: Bearer $INTERNAL_API_TOKEN` می‌خواهند. قرارداد در `shared/tmusic_common/indexer_contract.py` تعریف شده.

| POST | توضیح |
|---|---|
| `/internal/indexer/crawl/claim` | گرفتن کانال‌هایی که نوبت کرالشان رسیده، با lease |
| `/internal/indexer/crawl/batch` | یک صفحهٔ پارس‌شده: ingest + جابه‌جایی cursorها + منشن‌ها + آمار پارسر (idempotent) |
| `/internal/indexer/crawl/channels/{id}/release` | پس‌دادن کانال بدون پیشرفت (IP بلاک‌شده، ری‌استارت) |
| `/internal/indexer/crawl/channels/{id}/failure` | خطای کانال؛ `preview_disabled` مسیر fallback را فعال می‌کند |
| `/internal/indexer/crawl/candidates/claim` | کاندیدهایی که باید برای امتیازدهی اندازه‌گیری شوند |
| `/internal/indexer/crawl/candidates/{id}/stats` | نتیجهٔ اندازه‌گیری یک کاندید (سهم صوتی، نرخ پست، مشترک) |
| `/internal/indexer/accounts` | ثبت تنها session resolver و دریافت id |
| `/internal/indexer/accounts/{id}/report` | وضعیت اکانت resolver (cooling، limited، banned و FloodWait) |

در جهت عکس، core این مسیرها را روی edge صدا می‌زند (همان توکن):

| POST | توضیح |
|---|---|
| `/internal/resolve` | یک پیام، یک فایل: `file_unique_id`، مدت، حجم و mime — پایهٔ resolve تنبل |
| `/internal/send` | آپلود ترک به چت کاربر با Bot API |
| `/internal/probe` | خواندن تگ ID3 از اولین بایت‌های فایل |

`POST /tg/webhook` هم فقط از مسیر edge قابل دسترسی است و هدر `X-Telegram-Bot-Api-Secret-Token` را بررسی می‌کند.

## گروه‌های endpoint

| پیشوند | محتوا | فاز |
|---|---|---|
| `/v1/auth/*` | ورود با initData، refresh، logout | ۱ |
| `/v1/channels/*`, `/v1/library/*`, `/v1/artists/*` | کانال‌ها و کتابخانه | ۲–۳ |
| `/v1/search*` | جستجو (Meilisearch + فینگلیش، fallback روی pg_trgm) | ۳ |
| `/v1/tracks/{id}/stream` | تیکت امضاشدهٔ پخش (TTL کوتاه، Range در edge) | ۳ |
| `/v1/playlists/*`, `/v1/me/*` | پلی‌لیست، لایک، تاریخچه، وضعیت پخش | ۵ |
| `/v1/plans`, `/v1/payments/*`, `/v1/me/subscription` | پلن‌ها، پرداخت، اشتراک | ۶ |
| `/v1/discover`, `/v1/trending`, `/v1/tracks/{id}/similar`, `/radio` | پیشنهادها | ۷ |
| `/v1/artists/{id}/this-is`, `/follow`, `/v1/me/artists` | «این X است» (ترتیب از Last.fm، آهنگ از آرشیو)، دنبال کردن خواننده | 0015 |
| `/v1/tracks/{id}/lyrics`, `/hide`, `/progress`, `/v1/me/hidden`, `/v1/me/private-session`, `/v1/me/in-progress` | متن آهنگ، پنهان کردن آهنگ، جلسهٔ خصوصی، ادامهٔ فایل‌های طولانی | 0015 |
| `/v1/recommendations/for-tracks`, `/v1/playlists/{id}/recommendations` | پیشنهاد برای یک پلی‌لیست (Enhance) و شافل هوشمند | 0015 |
| `/v1/folders/*`, `/v1/playlists/{id}/folder` | پوشهٔ پلی‌لیست‌ها | 0015 |
| `/v1/blends/*`, `/v1/daylist`, `/v1/dj` | بلند (پلی‌لیست دونفره)، دی‌لیست، دی‌جی | 0015 |
| `/v1/jams/*` | جم: گوش دادن هم‌زمان با دوستان (صف و playhead مشترک در Redis) | جم |
| `/admin/*` | پنل مدیریت (توکن نوع `admin`) | ۸ |
| `/admin/candidates*`, `/admin/channels/import`, `/admin/crawler/*` | صف کانال‌های پیشنهادی، import دسته‌ای، سلامت کرالر/پارسر/resolver | ADR-002 |
| `/healthz`, `/readyz`, `/metrics` | سلامت و متریک (فقط شبکهٔ داخلی) | ۱، ۹ |

`/v1/payments/callback/{provider}` بدون احراز هویت است — درگاه صدایش می‌زند، نه کاربر —
و به همین دلیل هیچ‌وقت به ورودی‌اش اعتماد نمی‌کند: پرداخت با شناسهٔ خودش پیدا و
دوباره از درگاه verify می‌شود.

## جم (گوش دادن با هم)

یک صف مشترک و یک playhead که در Redis نگه داشته می‌شود (TTL شش ساعت، با هر تغییر تمدید).
playhead به شکل «کجا بود و کِی» (`pos` در زمان سرور `at`) ذخیره می‌شود، نه عددی که مدام گزارش شود؛
پس هر خواننده جای فعلی موزیک را حساب می‌کند و سرور وقتی آهنگی تمام شد خودش به بعدی می‌رود —
حتی وقتی گوشی میزبان قفل است.

| endpoint | کار |
|---|---|
| `POST /v1/jams` | ساختن جم با آنچه الان پخش می‌شود (`track_ids`، `index`، `position_s`، `playing`) |
| `GET /v1/jams/current` | جمی که کاربر در آن است، یا `null` |
| `GET /v1/jams/{code}?qrev=N` | وضعیت؛ اگر `qrev` همان باشد صف (`items`) برنمی‌گردد |
| `POST /v1/jams/{code}/join` · `/leave` | پیوستن و خروج (خروج میزبان یعنی پایان جم) |
| `DELETE /v1/jams/{code}` | پایان جم (فقط میزبان) |
| `POST /v1/jams/{code}/control` | `play`/`pause`/`seek`/`next`/`previous`/`jump`؛ با `expected_index` فقط یک بار skip می‌شود |
| `POST /v1/jams/{code}/queue` | افزودن آهنگ توسط هر عضو (`next`، `end`، یا `now` برای کنترل‌کننده‌ها) |
| `DELETE /v1/jams/{code}/queue/{index}` | حذف: میزبان هر چیزی، مهمان فقط آنچه خودش اضافه کرده |
| `PATCH /v1/jams/{code}` | `guests_can_control` (فقط میزبان) |

در بات: `/jam` جم می‌سازد (یا جم فعلی را نشان می‌دهد) با دکمه‌های ورود، دعوت و پایان؛
`/jam <code>` و لینک `t.me/<bot>?start=jam_<code>` به جم می‌پیوندند، و لینک
`t.me/<bot>?startapp=jam_<code>` مستقیم مینی‌اپ را روی همان جم باز می‌کند.
