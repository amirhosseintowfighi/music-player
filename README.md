# Telegram Music Mini App

یک پخش‌کننده برای کانال‌های موزیک تلگرام: کانال‌هایت را وصل کن، همه‌جا سرچ کن، پلی‌لیست بساز و داخل مینی‌اپ گوش بده.

| سند | محتوا |
|---|---|
| **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)** | **از صفر تا پخش اولین آهنگ — از اینجا شروع کن** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | معماری، توپولوژی ایران/خارج، بودجه، ریسک‌ها |
| [docs/DB.md](docs/DB.md) | اسکیمای دیتابیس |
| [docs/API.md](docs/API.md) · [openapi.json](docs/openapi.json) | قرارداد API |
| [docs/adr/](docs/adr/README.md) · [ADR-002](docs/ADR-002-indexing-strategy.md) · [ADR-003](docs/ADR-003-playback-and-metadata.md) | تصمیم‌های فنی (ADR) — ۰۰۲ منبع کاتالوگ را عوض می‌کند، ۰۰۳ نقشهٔ پخش و متادیتا است |
| [docs/DEPLOY.md](docs/DEPLOY.md) | استقرار روی سرور واقعی |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | کارهای عملیاتی و پاسخ به هشدارها |
| [docs/design/](docs/design/README.md) | سه گزینهٔ طراحی Home و تصمیم نهایی |

## وضعیت

| فاز | وضعیت |
|---|---|
| ۰ معماری | ✅ |
| ۱ اسکلت، داکر، احراز هویت، مدل داده، CI | ✅ |
| ۲ ایندکس کاتالوگ، backfill و آپدیت افزایشی، dedup، نرمال‌سازی فارسی، استخراج خواننده | ✅ |
| ۳ API کتابخانه، سرچ (Meilisearch + فینگلیش)، پخش با Range | ✅ |
| ۴ دیزاین سیستم Liquid Glass، Home/Search/Library، پخش‌کننده | ✅ |
| ۵ پلی‌لیست، لایک، تاریخچه، اشتراک‌گذاری | ✅ |
| ۶ اشتراک و پرداخت (استارز، درگاه ایرانی، کارت‌به‌کارت) | ✅ |
| ۷ پیشنهاددهی: CF، Trending، Discover Weekly، Daily Mix، رادیو | ✅ |
| ۸ پنل ادمین (داشبورد، کاربران، پرداخت، پیام همگانی، محتوا، سیستم) | ✅ |
| ۹ بهینه‌سازی، k6، مانیتورینگ، سخت‌سازی، مستندات | ✅ |
| + شبکهٔ اجتماعی (§۵)، اعلان‌ها (§۵)، تگ ID3، manifest های k8s (§۳) | ✅ |
| **بازطراحی ایندکس ([ADR-002](docs/ADR-002-indexing-strategy.md))**: کرالر پیش‌نمایش وب، resolve تنبل، کشف کانال، صفحات کرالر در پنل، حذف pool اکانت | ✅ |
| ۱۰ تا ۱۳ پخش و متادیتا ([ADR-003](docs/ADR-003-playback-and-metadata.md)) | ⏳ منتظر تأیید |

## ساختار

```
backend/   FastAPI (api) + aiogram (bot webhook) + arq (worker) + Alembic
indexer/   سرویس edge: کرالر پیش‌نمایش وب + resolver تک‌اکانته + سرور استریم
miniapp/   مینی‌اپ کاربر: React 19 + Vite + Tailwind (Liquid Glass، فارسی/انگلیسی، RTL)
admin/     پنل مدیریت: React + Vite، ورود با Telegram Login Widget
shared/    کد مشترک: قرارداد core↔edge، ticket استریم، لاگ JSON
infra/     compose برای prod (core و edge)، nginx، WireGuard، مانیتورینگ، k6، اسکریپت‌ها
bruno/     کالکشن Bruno برای تست دستی API
docs/      مستندات
```

## شروع سریع

روی یک سرور تازه، همین دو خط کافی است:

```bash
git clone https://github.com/amirhosseintowfighi/music-player.git
cd music-player && bash scripts/install.sh
```

و اگر فقط تنظیمات را می‌خواهی و بقیه را خودت می‌زنی:

```bash
python3 scripts/setup.py
```

ویزارد پیش‌نیازها را چک می‌کند، **همهٔ کلیدها را خودش می‌سازد** (هیچ‌کدام را تایپ
نمی‌کنی)، چند سؤال می‌پرسد که فقط تو جوابش را می‌دانی، `.env` را می‌نویسد و می‌گوید
قدم بعدی چیست. راهنمای کامل: [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md).

```bash
python3 scripts/setup.py --check                      # فقط بررسی ماشین
python3 scripts/setup.py --profile core --output .env.core
python3 scripts/setup.py --profile edge --output .env.edge
SETUP_LANG=en python3 scripts/setup.py                # انگلیسی
```

## اجرای کامل با Docker

اگر ترجیح می‌دهی دستی تنظیم کنی: `cp .env.example .env`، بعد `BOT_TOKEN`،
`BOT_USERNAME`، `WEBAPP_URL` و `MEILI_API_KEY` را پر کن و کلیدها را با این دستور
بساز و در `.env` بگذار:

```bash
docker compose run --rm --no-deps api python -m app.cli gen-keys
```

بعد:

```bash
docker compose up --build
```

- API: http://localhost:8000/docs
- edge استریم (nginx با کش): http://localhost:8081
- ثبت edge در دیتابیس، برای اینکه API بتواند لینک پخش بسازد:

```bash
docker compose exec api python -m app.cli add-edge http://localhost:8081
```

- دادهٔ نمایشی:

```bash
docker compose exec api python -m app.cli seed-demo
```

- ساختن یک initData معتبر برای تست دستی (فقط در dev):

```bash
docker compose exec api python -m app.cli dev-login 12345
```

- وارد کردن دسته‌ای کانال‌ها برای کرال (ADR-002؛ از فایل یا stdin):

```bash
docker compose exec api python -m app.cli seed-channels channels.txt
```

- لاگین **تنها** اکانت resolver (تعاملی؛ session رمزشده ذخیره می‌شود). کرالر بدون
  این هم کار می‌کند؛ فقط پخش ترک‌های هنوز resolve‌نشده به آن نیاز دارد:

```bash
docker compose run --rm edge python -m tmusic_indexer.login acc1
```

## اجرای تست‌ها بدون Docker

به PostgreSQL 16 (با `pg_trgm` و `citext`) و Meilisearch v1.53 نیاز است. اگر Meilisearch در دسترس نباشد، تست‌های سرچ skip می‌شوند.

```bash
uv venv --python 3.12 .venv
```

```bash
uv pip install -r backend/requirements-dev.lock -r indexer/requirements-dev.lock
```

```bash
uv pip install --no-deps -e shared -e backend -e indexer
```

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/tmusic_test TEST_MEILI_URL=http://127.0.0.1:7700 TEST_MEILI_KEY=<key> pytest --cov=app
```

```bash
cd indexer && pytest --cov=tmusic_indexer
```

بررسی‌های CI (در هر دو پوشه): `ruff check`، `ruff format --check`، `mypy` (strict).

## استقرار

توپولوژی هیبرید است: core در ایران و edge در خارج، با WireGuard بینشان (ADR-0002). فایل‌ها در `infra/compose/core.yml` و `infra/compose/edge.yml` هستند. راهنمای کامل در [docs/DEPLOY.md](docs/DEPLOY.md) و [docs/RUNBOOK.md](docs/RUNBOOK.md) است. نکته‌های لازم برای راه‌اندازی اولیه:

1. WireGuard را از روی `infra/wireguard/*.example` تنظیم کن.
2. گواهی TLS را در `infra/certs/` قرار بده (`fullchain.pem` و `privkey.pem`).
3. روی هر دو سرور، `"userland-proxy": false` را در `/etc/docker/daemon.json` بگذار تا IP واقعی کلاینت به nginx برسد.
4. روی core این دستور را اجرا کن. `TG_API_BASE` باید به egress روی edge اشاره کند:

```bash
docker compose -f infra/compose/core.yml --env-file .env.core up -d
```

5. روی edge:

```bash
docker compose -f infra/compose/edge.yml --env-file .env.edge up -d
```

6. ثبت webhook:

```bash
docker compose -f infra/compose/core.yml exec api python -m app.bot.set_webhook
```

## تست‌ها

```bash
cd backend && pytest -q --cov=app          # ۴۳۵ تست، پوشش ~۹۴٪
cd indexer && pytest -q                    # ۱۱۵ تست
cd miniapp && npm test && npm run build    # ۷۷ تست + بودجهٔ باندل
cd admin   && npm test                     # ۲۴ تست
```

تست بار (هدف §۹: ۱۰٬۰۰۰ کاربر همزمان): [`infra/k6/README.md`](infra/k6/README.md).

تست بار کرالر (backfill یک کانال ۲۰٬۰۰۰ پیامی — نتیجه در
[ADR-002 §۹](docs/ADR-002-indexing-strategy.md)):

```bash
cd backend && LOADTEST=1 pytest tests/load -s -q
```
