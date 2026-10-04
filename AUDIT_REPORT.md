# گزارش جامع ممیزی پروژه music-player

> **تاریخ:** ۱۴۰۳/۰۷/۱۲ — 2026-10-04 &nbsp;|&nbsp; **شاخه:** main &nbsp;|&nbsp; **کامیت:** a91a73f  
> **روش:** ممیزی ایستا + خوانش ۸۶ فایل بک‌اند، miniapp/admin/indexer/infra/compose/CI + سه ممیزی موازی (Backend / Frontend & Design / Infra & Performance) + تحلیل k6

---

## فهرست

1. [چکیده مدیریتی](#چکیده-مدیریتی)
2. [بک‌اند](#۱-بک‌اند-fastapi--arq--postgresredisemeili)
3. [فرانت و دیزاین](#۲-فرانت-و-دیزاین-miniapp--admin)
4. [زیرساخت و استقرار](#۳-زیرساخت-استقرار-و-کارایی)
5. [کارایی و بنچمارک](#۴-کارایی-و-بنچمارک)
6. [نقشه راه و چک‌لیست](#۵-نقشه-راه-و-چکلیست)

---

## چکیده مدیریتی

پروژه در رده **تولید-آماده با بدهی قابل‌مدیریت** قرار دارد.

معماری هیبرید ایران/خارج، تفکیک core/edge، مستندسازی ADR و تست‌گاردها در سطح نادر تمیزی است؛ فرانت Liquid Glass و موتور پخش gapless حرفه‌ای است؛ اینفرا با WireGuard، کش slice، مانیتورینگ ۱۸ هشداری و بکاپ با drill، از میانگین بازار جلوتر است.

> **سه ریسک P0 اگر ترافیک واقعی بگیرد همین فردا می‌شکند؛ بقیه بدهی بهره‌دارِ قابل زمان‌بندی است.**

| محور | امتیاز | یک‌خط |
|---|:---:|---|
| معماری | ⭐⭐⭐⭐⭐ ۹.۵ | هیبرید مستند = پیاده، stateless + arq |
| مدل داده | ⭐⭐⭐⭐⭐ ۹ | پارتیشن، partial unique، dedup دومرحله‌ای |
| طراحی API | ⭐⭐⭐⭐ ۸ | خطای یکنواخت، keyset، forbid |
| امنیت | ⭐⭐⭐⭐ ۸ | HMAC/JWT/scrypt درست؛ ضعف‌ها عملیاتی‌اند |
| فرانت / دیزاین | ⭐⭐⭐⭐ ۸.۵ | Liquid Glass درست‌فهم‌شده + توکن سمنتیک |
| دسترس‌پذیری | ⭐⭐ ۵ | بدون focus-trap / ErrorBoundary |
| اینفرا / استقرار | ⭐⭐⭐⭐ ۸.۵ | ۳ پروفایل compose + اسکریپت‌های جادویی |
| مشاهده‌پذیری | ⭐⭐⭐⭐⭐ ۹ | Prometheus + Grafana + Alertmanager کامل |
| CI | ⭐⭐⭐⭐⭐ ۹ | ruff + mypy strict + coverage 85% |
| بنچمارک | ⭐⭐⭐ ۶.۵ | k6 دوگانه خوب؛ بدون پرداخت / cold-cache |

> اگر P0ها همین اسپرینت بسته شود، سقف طراحی ARCH §۱۰ (۲۰۰k کاربر / ۱۲۰۰ استریم همزمان / p95 API <۲۰۰ms) دست‌یافتنی است.

### اولویت‌بندی کلان

- 🔴 **P0 — همین اسپرینت:** W1 / W2 / W3 / W-P0-1..3 / نبود limits + بکاپ off-site
- 🟠 **P1 — ۱-۲ اسپرینت:** کش توزیع‌شده، TrustedProxy، pick_edge RTT، Meili fallback، شکستن player.ts، virtualization
- 🟡 **P2 — میان‌مدت:** trace انتشار، audit ip، soak، سناریو پرداخت، Loki، Storybook

---

## ۱. بک‌اند (FastAPI + arq + Postgres/Redis/Meili)

### نقاط قوت

- **لایه‌بندی شفاف:** `api/routers → services → domain/text → infra`; منطق زبان/فینگلیش خالص و تست‌پذیر در `backend/app/domain/text/`.
- **قرارداد edge/core یک‌جا:** `shared/tmusic_common/indexer_contract.py` منبع حقیقت؛ `httpx.AsyncClient` مشترک در `AppState`
- **مدل داده حساب‌شده:** `bigint GENERATED ALWAYS`, `CITEXT`, پارتیشن ماهانه `play_history/audit_log`، هویت دومرحله‌ای ترک (`file_unique_id` + `source_key = "<channel>:<msg>"` + CHECK)، dedup فازی (`normalized_artist + duration ±2s + trigram`) قابل‌تنظیم از flag
- **API منضبط:** `AppError(code/status/details)` + نگاشت متمرکز، keyset opaque، `ApiModel(extra=forbid)`, `docs_url` فقط non-prod
- **امنیت کریپتو درست:** `initData` با `parse_qsl(strict)` + `HMAC WebAppData` + `compare_digest`; LoginWidget با `SHA256(bot_token)` + `typ admin/access` جدا؛ JWT EdDSA ۱۵د + refresh چرخشی با family revoke؛ بن فوری via Redis `banned_users`; رمز ادمین `scrypt N=2^16`
- **پرفورمنس آگاه:** PgBouncer transaction mode درست (`statement_cache_size=0`), بدون N+1 (hydrate ۳ کوئری، ingest set-based با `xmax=0`), کش چندلایه, تیکت HMAC + انتخاب edge وزن‌دار
- **پرداخت idempotent:** `UNIQUE(provider,provider_ref) WHERE NOT NULL` + `FOR UPDATE` + verify سمت‌سرور + pro-rate ریالی + audit
- **تست‌هارنس جدی:** Postgres واقعی + fakeredis + Meili واقعی, `QueryCounter`, `assert_max_queries`, نگهبان `test_no_bytes_through_core`

### ایرادات بحرانی (P0)

| کد | عنوان | محل دقیق | پیامد |
|---|---|---|---|
| W1 | گیت تعمیرات روی hot path | `api/main.py:90` + `api/deps.py:79` | هر درخواست یک SELECT؛ زیر کندی DB خودش عامل قطعی |
| W2 | سقف روزانه race | `services/stream.py:145` sadd→scard→srem نااتمیک | عبور از سقف تا N× concurrency |
| W3 | کلید امضای استریم بدون ولیدیشن | `config.py:104` split خام | کلید خالی/کوتاه → IndexError و خواب کل پخش |
| W4 | rate-limit پنجره ثابت | `api/deps.py:66` incr+expire 65s | burst ‎2× در مرز دقیقه؛ دورخوردن با IP چرخشی |

```lua
-- راه‌حل W2: اتمیک با Lua (EVAL)
local added = redis.call('SADD', KEYS[1], ARGV[1])
if added==1 then redis.call('EXPIRE', KEYS[1], 172800) end
local n = redis.call('SCARD', KEYS[1])
if n > tonumber(ARGV[2]) then redis.call('SREM', KEYS[1], ARGV[1]); return 0 end
return 1
```

### مهم (P1)

- W5 کش لوکال flag/plan با TTL ۳۰-۶۰ث → staleness بین رپلیکا. راه‌حل: Redis + pub/sub invalidate
- W6 `client_ip = request.client.host` شکننده پشت nginx. راه‌حل: TrustedProxyMiddleware با X-Forwarded-For
- W7 انتخاب edge بدون Lock، وزن RTT در نظر گرفته نمی‌شود + بدون متریک
- W8 Meili 4xx → RuntimeError به‌جای fallback → ۵۰۰
- W9 `bot_token/tg_api_base` بدون validator
- W10 callback پرداخت حتی برای card2card/۵۰۰ هم ۳۰۳ به ربات
- W11 مهاجرت آفلاین SystemExit مانع `alembic --sql`

---

## ۲. فرانت و دیزاین (miniapp + admin)

**مختصر:** Mini App در سطح محصول؛ الگوی Apple Music + Liquid Glass با توکن سمنتیک، موتور پخش دو-element gapless، RTL درجه‌یک، بودجه باندل ۲۰۰KB با guard.

### نقاط قوت

- `miniapp/src/design/tokens.css`: هرگز رنگ لفظی نه؛ فقط `--separator/--fill/--ink`; قانون «شیشه فقط chrome شناور» خوانایی را نجات داده؛ `[data-theme]` + fallback material-solid
- سه‌لایه Aurora→محتوا→chrome + specular با اسکرول؛ نگهبان `fragileWebView()/deviceLooksSlow()/watchFrameRate 45fps` + سوییچ auto/high/low در Settings
- Tailwind v4 بدون config، `@utility glass`, vendor chunking, lazy برای FullPlayer + ۸ اسکرین
- React 19.3 + Vite 8.3 + TanStack Query 5 + Zustand 5 + tsconfig سخت‌گیر
- بودجه باندل `scripts/check-size.mjs` (gzip ۲۰۰KB fail) روی فایل‌های لینک‌شده
- Zustand اسلایس (player/ui/jam/audio) + صف دوگانه source/manual + MediaSession
- i18n با ۴۲۶ کلید و تضمین برابری fa/en، RTL پویا، سوایپ/Scrubber RTL-aware
- engine دو audio (active/spare) + crossfade + unlock WAV برای iOS
- `public/boot.js` ES5 + تشخیص کرش + diagnostics

### ایرادات بحرانی (P0)

- **W-P0-1/2:** Sheet بدون focus-trap، بدون بازگشت فوکوس، بدون body scroll-lock/overscroll-contain → شکست a11y روی TalkBack
- **W-P0-3:** فقدان ErrorBoundary سراسری و مرزی → یک خطا کل اپ سفید

### مهم (P1)

- `store/player.ts` ۸۹۸ خط God Store
- a11y ناقص: TrackRow بدون aria-label، Scrubber انگلیسی هاردکد، Toggle بدون labelledby
- framer-motion با prefers-reduced-motion همچنان می‌دود → نیاز MotionConfig
- واگرایی توکن سه‌گانه (miniapp قرمز، docs سبزآبی، admin teal) بدون بسته مشترک
- Library/Search بدون virtualization (۵۰۰ ترک = ۵۰۰ DOM)
- Vazirmatn بدون @font-face/preload
- Admin بدون i18n، متون هاردکد

---

## ۳. زیرساخت، استقرار و کارایی

### نقاط قوت

- **۳ پروفایل compose:** dev (127.0.0.1+reload) / solo (ghcr pull + certbot helper) / core+edge با WireGuard 10.8.0.1/24
- **ایمیج:** یک ایمیج backend برای api/worker/migrate، gha cache، logging 50m×5
- **اسکریپت‌ها:** `install.sh` wizard + `setup.py` بدون وابستگی (Ed25519, chmod 600) + `deploy.sh` با flock/dump pre-deploy/migrate قبل از up + rollback
- **Nginx slice کش:** `proxy_cache_path stream:200m slice 1m`, `cache_key s:$track_id:$slice`, resolver پویا, auth_request, cache_lock, X-Cache
- **DB:** Postgres 16 tuning (3GB/9GB, archive_mode), سه نقش least-privilege, PgBouncer 4000/40 scram, Redis noeviction, Meili v1.53.2
- **مانیتورینگ:** Prometheus 15s, alerts.yml ۱۸ هشدار, Grafana ۱۵ پنل, Alertmanager با ربات جدا + inhibit
- **بکاپ:** pg_dump Fc + validate + retention 14d + textfile metrics + restore-test هفتگی

### ایرادات بحرانی

| شدت | عنوان | محل | پیامد |
|---|---|---|---|
| 🔴 | نبود resource limits در compose | `infra/compose/*` | OOM روی prod (فقط k8s limits دارد) |
| 🔴 | بکاپ فقط لوکال | `backup.sh` | سوختن هاست = از دست رفتن بکاپ/WAL |
| 🟠 | تمدید TLS دستی | `solo.yml` | انقضا ۹۰ روزه → قطعی |
| 🟠 | prometheus host network + target api:8000 | `core.yml` | DNS bridge دیده نمی‌شود → سکوت هشدار |
| 🟠 | Redis solo بدون maxmemory؛ pgbouncer/meili بدون healthcheck | `core.yml` | رشد بی‌نهایت، readyz دروغ |

### بنچمارک k6 — موجود و شکاف

- **موجود:** `infra/k6/load.js` (ramping-vus, VUS/BASE env, groups home/search/play, thresholds p95 API<200ms/search<100ms/failed<1%) + `stream.js` (30 VUs Range 0-256KB + seek, TTFB p95<800ms)
- **شکاف:** بدون تست پرداخت/ادمین/وبهوک؛ stream بدون flow login؛ بدون cold vs warm cache و fallback pg_trgm؛ VUS پیش‌فرض ۵۰۰ vs مستند ۲۰۰۰؛ بدون soak چندساعته

---

## ۴. کارایی و بنچمارک — تحلیل سریع

| سناریو k6 | هدف ARCH | وضعیت امروز | گلوگاه |
|---|---|---|---|
| home batch (۴ موازی) | p95 <200ms | با کش و بدون W1 قابل دستیابی | W1 + کش لوکال |
| search | p95 <100ms | Meili سالم OK؛ fallback pg_trgm می‌پرد | W8 |
| stream slice 256KB | TTFB p95 <800ms | با edge وزن‌دار و HIT ~۵۰ms | W2 + W7 |
| ۱۰k همزمان | ۴-۶ host | نیاز ۲k VU/host | limits + PgBouncer pool 40 |

بودجه فرانت ۲۰۰KB gzip؛ framer-motion بزرگ‌ترین سهم — LazyMotion ~۳۰KB صرفه‌جویی.

---

## ۵. نقشه راه و چک‌لیست

### اسپرینت بعد (P0) — قبل از مقیاس

- [ ] maintenance flag به Redis + Lua سقف روزانه + validator کلید امضا
- [ ] MotionConfig + ErrorBoundary + FocusTrap/ScrollLock + aria بومی
- [ ] resource limits در compose + کرون certbot renew + off-site بکاپ (S3/WAL-G)

### ۱-۲ اسپرینت (P1)

- [ ] کش توزیع‌شده flag/plan + TrustedProxy + pick_edge RTT-aware + Meili fallback
- [ ] شکستن player.ts + virtualization + توکن مشترک + Vazirmatn preload
- [ ] فیکس prometheus networking + healthcheck pgbouncer/meili + exporter + purge-track

### میان‌مدت (P2)

- [ ] trace_id به worker/edge + پرکردن audit_log.ip + soak + سناریو پرداخت + Loki + Storybook

### چک‌لیست PR بعدی

- [ ] `k6 run load.js` قبل/بعد W1 p95 <200ms
- [ ] `asyncio.gather 20` روی مرز سقف روزانه سبز (Lua)
- [ ] `Settings()` بدون کلید امضا بالا نمی‌آید
- [ ] `MotionConfig reducedMotion=user` + axe a11y روی Sheet
- [ ] `docker compose config` limits دارد + backup به S3 + cert renew کرون فعال

---

## جمع‌بندی

بک‌اند «معماری مستند=پیاده» و فرانت «Liquid Glass درست‌فهم‌شده» را کمتر پروژه‌ای هم‌زمان دارد؛ اینفرا هم تمیزترین Compose سه‌پروفایله‌ای است که دیده شده. ۸۰٪ کار سخت تمام شده؛ ۲۰٪ باقی‌مانده دقیقاً همین P0هاست که با ۲-۳ PR بسته می‌شود و بعد پروژه برای audit دسترس‌پذیری AA و تست میان‌رده اندروید و بار ۱۰k آماده است.

---
*این گزارش به صورت خودکار توسط ممیزی موازی سه‌عامله تولید شد. برای پوش به ریپو: دسترسی توکن را به Read & Write ارتقا دهید.*
