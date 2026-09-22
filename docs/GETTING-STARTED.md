# راه‌اندازی از صفر

این سند از «هیچی ندارم» شروع می‌کند و به «ربات و مینی‌اپ بالا آمده و آهنگ پخش
می‌شود» می‌رسد. اگر فقط می‌خواهی روی لپ‌تاپ خودت ببینی‌اش، بخش
[۱](#۱-راه‌اندازی-محلی-ده-دقیقه) کافی است. برای سرور واقعی، بخش
[۳](#۳-استقرار-واقعی-دو-سرور) را هم بخوان.

- [۰. چه چیزی لازم داری](#۰-چه-چیزی-لازم-داری)
- [۱. راه‌اندازی محلی (ده دقیقه)](#۱-راه‌اندازی-محلی-ده-دقیقه)
- [۲. اولین کانال‌ها و اولین آهنگ](#۲-اولین-کانال‌ها-و-اولین-آهنگ)
- [۳. استقرار واقعی (دو سرور)](#۳-استقرار-واقعی-دو-سرور)
- [۴. پنل ادمین](#۴-پنل-ادمین)
- [۵. توسعه بدون داکر](#۵-توسعه-بدون-داکر)
- [۶. وقتی چیزی کار نمی‌کند](#۶-وقتی-چیزی-کار-نمی‌کند)

---

## ۰. چه چیزی لازم داری

| چیز | برای چه | اجباری؟ |
|---|---|---|
| Docker + Docker Compose | اجرای همه‌چیز | بله |
| یک ربات تلگرام (از [@BotFather](https://t.me/BotFather)) | خود محصول | بله |
| Python 3.12 | فقط برای اجرای ویزارد نصب | بله (روی اکثر سیستم‌ها هست) |
| یک دامنه با SSL | مینی‌اپ فقط روی HTTPS باز می‌شود | برای سرور واقعی |
| یک شمارهٔ تلگرام | فقط برای **اولین** پخش ترک‌هایی که هنوز resolve نشده‌اند | نه |

نکتهٔ مهم: **کاتالوگ به هیچ اکانت تلگرامی نیاز ندارد.** آهنگ‌ها از صفحهٔ عمومی
`t.me/s/<channel>` خوانده می‌شوند ([ADR-002](ADR-002-indexing-strategy.md)). آن یک
شماره فقط برای لحظه‌ای لازم است که کاربری اولین بار یک ترک خاص را پخش می‌کند.

### ساختن ربات

۱. در تلگرام به [@BotFather](https://t.me/BotFather) بگو `/newbot`، اسم و یوزرنیم
   بده، و **توکن** را نگه دار.
۲. `/setdomain` را بزن و دامنهٔ مینی‌اپ را بده (مثلاً `https://app.example.com`).
   بدون این، مینی‌اپ باز نمی‌شود.
۳. اگر می‌خواهی دکمهٔ منو مستقیم مینی‌اپ را باز کند: `/setmenubutton`.

---

## ۱. راه‌اندازی محلی (ده دقیقه)

**یک دستور، از صفر تا بالا آمدن:**

```bash
git clone https://github.com/amirhosseintowfighi/music-player.git
cd music-player && bash scripts/install.sh
```

`install.sh` پیش‌نیازها را چک می‌کند، ویزارد را اجرا می‌کند، ایمیج‌ها را می‌سازد،
سرویس‌ها را بالا می‌آورد، منتظر می‌ماند تا API واقعاً جواب بدهد و تو را ادمین
می‌کند. هر قدمش دستوری است که خودت هم می‌توانستی بزنی؛ چیزی پنهان نیست.

اگر می‌خواهی قدم‌به‌قدم خودت پیش بروی، فقط ویزارد را صدا بزن:

```bash
python3 scripts/setup.py
```

ویزارد این کارها را می‌کند:

1. **ماشین را چک می‌کند** — داکر، کامپوز، پایتون، و اینکه پورت‌های لازم آزادند.
2. **همهٔ کلیدها را خودش می‌سازد** — کلید Ed25519 برای JWT، کلید امضای استریم،
   توکن داخلی core↔edge، رمز دیتابیس و بقیه. هیچ‌کدام را تو تایپ نمی‌کنی و هیچ‌کدام
   از ماشینت خارج نمی‌شود.
3. **چند سؤال می‌پرسد** که فقط تو جوابش را می‌دانی: توکن ربات، یوزرنیم ربات،
   دامنه، و شناسهٔ عددی تلگرام خودت (از [@userinfobot](https://t.me/userinfobot)).
4. **فایل `.env` را می‌نویسد** — با همان کامنت‌ها و ترتیب `.env.example`، و اگر
   فایلی از قبل باشد بدون اجازه بازنویسی‌اش نمی‌کند.
5. **می‌گوید قدم بعدی چیست.**

سوئیچ‌های مفید:

```bash
python3 scripts/setup.py --check                      # فقط بررسی پیش‌نیازها
python3 scripts/setup.py --profile core --output .env.core
python3 scripts/setup.py --profile edge --output .env.edge
SETUP_LANG=en python3 scripts/setup.py                # انگلیسی
```

بعد از ویزارد:

```bash
docker compose up -d --build                                    # همه‌چیز بالا می‌آید
docker compose exec api python -m app.cli add-admin <tg-id> --apply   # خودت را ادمین کن
```

مهاجرت دیتابیس لازم نیست دستی اجرا شود: سرویس `migrate` قبل از `api` بالا می‌آید و
`alembic upgrade head` را خودش می‌زند. (اگر خواستی دستی: `docker compose run --rm
migrate`.)

`add-admin` بدون `--apply` فقط SQL را چاپ می‌کند؛ با `--apply` خودش در دیتابیس
می‌نویسد.

و یک قدم که فراموشش playback را می‌شکند — معرفی edge استریم، چون API باید بداند
لینک پخش را به کجا بدهد (`install.sh` خودش این کار را می‌کند):

```bash
docker compose exec api python -m app.cli add-edge http://localhost:8081 100
```

آدرسی که اینجا می‌دهی همان است که **مرورگر کاربر** صدا می‌زند؛ روی سرور واقعی باید
آدرس عمومی باشد، نه `localhost`.

حالا سلامت را چک کن:

```bash
curl -fsS http://localhost:8000/healthz && echo
```

### روی یک سرور، برای کاربران واقعی — پروفایل `solo`

کامپوز توسعه همه‌چیز را فقط روی `127.0.0.1` باز می‌کند، TLS ندارد و فرانت‌اندها را
سرو نمی‌کند. برای یک سرور واقعی، پروفایل `solo` هست: همان کد، ولی با nginx و TLS و
هر دو فرانت‌اند، همه روی یک ماشین.

```bash
cd music-player && PROFILE=solo bash scripts/install.sh
```

این پروفایل خودش:

- مینی‌اپ و پنل ادمین را **داخل داکر** build می‌کند (روی سرور به Node نیاز نداری)؛
- یک گواهی self-signed موقت می‌سازد تا nginx بتواند بالا بیاید (این مرغ‌وتخم‌مرغِ
  «nginx بدون گواهی بالا نمی‌آید، certbot بدون nginx گواهی نمی‌گیرد» را باز می‌کند)؛
- کل استک را با nginx بالا می‌آورد: `app` و `admin` و `api` و `cdn` و `hook`؛
- edge را با آدرس عمومی `https://cdn.<دامنه>` ثبت می‌کند.

بعدش پنج رکورد DNS را به همین سرور بده:

```
app.<دامنه>   admin.<دامنه>   api.<دامنه>   cdn.<دامنه>   hook.<دامنه>
```

و یک بار گواهی واقعی بگیر:

```bash
C="docker compose -f infra/compose/solo.yml --env-file .env"
$C --profile certs run --rm certbot
$C exec nginx nginx -s reload
```

تمدید هم همین دو دستور است؛ در cron ماهانه بگذارش.

در BotFather هم `/setdomain` را روی `https://app.<دامنه>` تنظیم کن.

**فرق `solo` با استقرار دو سروری**: در `solo` وبهوک مستقیم به API می‌رود (تونلی در
کار نیست) و کرالر و استریم از همان ماشین بیرون می‌زنند. اگر سرور داخل ایران است،
باید به `t.me` و دیتاسنترهای تلگرام دسترسی داشته باشد. وقتی پهنای باند یا موقعیت
جغرافیایی ایجاب کرد، همان کد بدون تغییر به `core` و `edge` تقسیم می‌شود (بخش ۳) —
فقط `INTERNAL_API_TOKEN` و `STREAM_SIGNING_KEYS` باید بین دو طرف یکی باشد.

---

## ۲. اولین کانال‌ها و اولین آهنگ

کاتالوگ خودش پر نمی‌شود؛ باید بگویی کدام کانال‌ها را بخواند.

**راه اول — دسته‌ای از فایل** (برای شروع، ۳۰ تا ۵۰ کانال عمومی موزیک):

```bash
printf '%s\n' @Musicirani_Official @PersianOldies @SirvanMusic > channels.txt
docker compose exec -T api python -m app.cli seed-channels - < channels.txt
```

هر خط می‌تواند یوزرنیم، `@یوزرنیم`، لینک `t.me/...` یا یک ستون CSV باشد.

**راه دوم — از پنل ادمین**: صفحهٔ «کانال‌های پیشنهادی» → کادر «افزودن دسته‌ای».

**راه سوم — کاربر پیشنهاد می‌دهد**: داخل مینی‌اپ، کانالی که وجود ندارد وارد صف
کاندیدها می‌شود و ادمین تأیید/رد می‌کند.

بعد از چند دقیقه:

```bash
docker compose exec db psql -U tmusic -d tmusic -c \
  "SELECT username, status, crawl_status, tracks_count FROM channels ORDER BY id;"
```

وقتی `status` شد `active`، مینی‌اپ را باز کن (از دکمهٔ منوی ربات) و یک آهنگ بزن.

**اولین پخشِ هر ترک** ممکن است چند ثانیه طول بکشد: در آن لحظه فایل با MTProto
resolve می‌شود و از آن به بعد کش است. اگر اکانت resolver را لاگین نکرده باشی،
پیام «الان قابل پخش نیست» می‌گیری — این عمدی و صریح است، نه خرابی:

```bash
docker compose run --rm edge python -m tmusic_indexer.login acc1
```

job `prewarm_resolver` هر ده دقیقه ترک‌های محبوب و درخواست‌شده را از قبل resolve
می‌کند، تا کاربر عملاً هیچ‌وقت منتظر نماند.

---

## ۳. استقرار واقعی (دو سرور)

معماری عمداً دو تکه است ([ARCHITECTURE.md §۳](ARCHITECTURE.md)):

```
CORE  (ایران)   api, bot, worker, postgres, redis, meilisearch, admin
EDGE  (خارج)    کرالر + resolver + استریمر + nginx با کش ۴۰GB
                ↕ WireGuard
```

دلیلش ساده است: متادیتا باید از داخل ایران سریع باشد، و بایت‌های صوت نباید از
ایران رد شوند (هزینه و تأخیر). **API هرگز بایت صوت سرو نمی‌کند** — تستی در CI هست
که اگر روزی کسی این را عوض کند، build می‌شکند.

### روی سرور core

```bash
python3 scripts/setup.py --profile core --output .env.core
docker compose -f infra/compose/core.yml --env-file .env.core up -d
docker compose -f infra/compose/core.yml --env-file .env.core exec api alembic upgrade head
```

### روی سرور edge

```bash
python3 scripts/setup.py --profile edge --output .env.edge
```

ویزارد `INTERNAL_API_TOKEN` و `STREAM_SIGNING_KEYS` را از تو می‌پرسد: **باید دقیقاً
همان‌هایی باشند که در `.env.core` هستند**، وگرنه دو سرویس همدیگر را قبول نمی‌کنند.

```bash
docker compose -f infra/compose/edge.yml --env-file .env.edge up -d
```

بعد edge را به هسته معرفی کن و وبهوک را ثبت کن:

```bash
docker compose -f infra/compose/core.yml --env-file .env.core \
  exec api python -m app.cli add-edge cdn.example.com 100
docker compose -f infra/compose/core.yml --env-file .env.core \
  exec api python -m app.bot.set_webhook
```

جزئیات تونل، SSL، بکاپ و چک‌لیست بعد از استقرار در [DEPLOY.md](DEPLOY.md) است.

---

## ۴. پنل ادمین

`https://admin.<دامنه>` (یا در حالت محلی `http://localhost:5174`). ورود با
Telegram Login Widget و همان اکانتی که `add-admin` شده.

| صفحه | برای چه |
|---|---|
| داشبورد | DAU/WAU/MAU، درآمد، تبدیل، churn |
| کاربران | جست‌وجو، بن، هدیه، impersonate |
| پرداخت‌ها | تأیید کارت‌به‌کارت، بازپرداخت |
| گزارش‌ها | کپی‌رایت و تخلف، با SLA |
| **کانال‌های پیشنهادی** | صف کاندیدها با امتیاز، تأیید/رد تکی و گروهی، import دسته‌ای |
| **کرالر** | وضعیت هر کانال، پیشرفت backfill، سلامت پارسر، وضعیت resolver، کرال مجدد |
| **بازبینی متادیتا** | ترک‌هایی که پارسر مطمئن نبوده یا کاربر گزارش کرده، با اصلاح گروهی |
| سلامت / تنظیمات / لاگ | متریک‌ها، feature flagها، audit |

---

## ۵. توسعه بدون داکر

به PostgreSQL 16 (با `pg_trgm` و `citext`)، Redis و Meilisearch نیاز داری.

```bash
uv venv --python 3.12 .venv
uv pip install -r backend/requirements-dev.lock -r indexer/requirements-dev.lock
uv pip install --no-deps -e shared -e backend -e indexer

cd backend && alembic upgrade head && uvicorn app.api.main:create_app --factory --reload
cd miniapp && npm install && npm run dev
cd admin   && npm install && npm run dev
```

تست‌ها:

```bash
cd backend && pytest -q --cov=app     # ۵۵۰+ تست
cd indexer && pytest -q               # ۱۲۰+ تست
cd miniapp && npm test                # ۱۰۰+ تست
cd admin   && npm test                # ۲۹ تست
```

تست بار کرالر (backfill یک کانال ۲۰٬۰۰۰ پیامی):

```bash
cd backend && LOADTEST=1 pytest tests/load -s -q
```

سنجش دقت پارسر متادیتا روی ۶۱ عنوان واقعی فارسی:

```bash
cd backend && pytest tests/unit/test_title_corpus.py -s -q
```

---

## ۶. وقتی چیزی کار نمی‌کند

| نشانه | معمولاً یعنی | کجا را ببین |
|---|---|---|
| مینی‌اپ سفید می‌ماند | `/setdomain` در BotFather ست نشده، یا `WEBAPP_URL` غلط است | کنسول مرورگر |
| «الان قابل پخش نیست» | اکانت resolver لاگین نشده یا FloodWait خورده | [RUNBOOK → resolver مرده](RUNBOOK.md#resolver) |
| کانال‌ها کرال می‌شوند ولی ترکی اضافه نمی‌شود | تلگرام HTML پیش‌نمایش را عوض کرده | [RUNBOOK → پارسر شکسته](RUNBOOK.md#parser) |
| کانال در `preview_disabled` | کانال خصوصی است یا preview را بسته | از کاربر بخواه بات را ادمین کند |
| ربات جواب نمی‌دهد | وبهوک ثبت نشده | `getWebhookInfo` و [RUNBOOK](RUNBOOK.md#api-down) |
| خطای ۴۰۱ بین core و edge | `INTERNAL_API_TOKEN` دو طرف یکی نیست | `.env.core` و `.env.edge` |

بقیهٔ رویه‌های عملیاتی — بکاپ، چرخاندن کلیدها، حالت تعمیرات، کرال دستی یک کانال —
در [RUNBOOK.md](RUNBOOK.md) است.

---

## نقشهٔ مستندات

| سند | محتوا |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | معماری، توپولوژی ایران/خارج، بودجه، ریسک‌ها |
| [DB.md](DB.md) | اسکیمای دیتابیس، جدول‌به‌جدول |
| [API.md](API.md) · [openapi.json](openapi.json) | قرارداد API |
| [DEPLOY.md](DEPLOY.md) | استقرار روی سرور واقعی |
| [RUNBOOK.md](RUNBOOK.md) | «چه چیزی پیج می‌کند و چه کار کنم» |
| [ADR-002](ADR-002-indexing-strategy.md) | چرا کاتالوگ از پیش‌نمایش وب می‌آید |
| [ADR-003](ADR-003-playback-and-metadata.md) | پخش، پلیر، و کیفیت متادیتا |
| [adr/](adr/README.md) | بقیهٔ تصمیم‌های فنی |
