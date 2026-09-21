# DEPLOY

توپولوژی هدف در [ARCHITECTURE §۲](ARCHITECTURE.md) توضیح داده شده: **هسته در ایران**
(دیتابیس، ردیس، Meilisearch، API، ربات، ورکرها، پرداخت) و **edge در خارج**
(ایندکسر MTProto، استریمر، پروکسی خروجی تلگرام)، وصل‌شده با WireGuard.

---

## ۰. پیش‌نیازها

| مورد | حداقل |
|---|---|
| سرور هسته (ایران) | ۴ vCPU، ۱۶GB RAM، ۲۰۰GB NVMe |
| سرور edge (خارج) | ۲ vCPU، ۴GB RAM، ۴۰GB دیسک برای کش، پهنای‌باند بالا |
| دامنه | `api.` و `app.` و `admin.` و `hook.` و `cdn.` |
| گواهی TLS | Let's Encrypt (DNS-01 توصیه می‌شود) |
| اکانت تلگرام | یک ربات + (اختیاری) **یک** شمارهٔ واقعی برای resolver. کاتالوگ از پیش‌نمایش عمومی می‌آید و به هیچ اکانتی نیاز ندارد ([ADR-002](ADR-002-indexing-strategy.md)) |

---

## ۱. آماده‌سازی محیط

```bash
git clone <repo> /opt/tmusic && cd /opt/tmusic
cp .env.example .env.core        # روی سرور هسته
cp .env.example .env.edge        # روی سرور edge
docker run --rm ghcr.io/example/tmusic-backend:latest python -m app.cli gen-keys
```

خروجی `gen-keys` را در هر دو فایل بگذار. مقادیری که **باید** پر شوند:

- هسته: `DATABASE_URL`، `POSTGRES_PASSWORD`، `APP_DB_PASSWORD`، `BOT_TOKEN`،
  `BOT_USERNAME`، `WEBAPP_URL`، `WEBHOOK_SECRET`، `JWT_*`، `STREAM_SIGNING_KEYS`،
  `INTERNAL_API_TOKEN`، `PUBLIC_API_URL`، `MEILI_API_KEY`، `GRAFANA_PASSWORD`
- edge: `INTERNAL_API_TOKEN` و `STREAM_SIGNING_KEYS` (**دقیقاً همان مقادیر هسته**)،
  `TG_API_ID`/`TG_API_HASH`، `SESSION_ENCRYPTION_KEY`، `CORE_INTERNAL_URL`
- پرداخت: `ZARINPAL_MERCHANT_ID` / `IDPAY_API_KEY` / `NEXTPAY_API_KEY` و
  `PAYMENTS_ADMIN_CHAT_ID` (فقط آن‌هایی که واقعاً استفاده می‌کنی)

> کلیدهای درگاه هرگز در دیتابیس یا پنل ادمین ذخیره نمی‌شوند (ADR-0016).

---

## ۲. WireGuard

```bash
cp infra/wireguard/core-wg0.conf.example /etc/wireguard/wg0.conf   # روی هسته
cp infra/wireguard/edge-wg0.conf.example /etc/wireguard/wg0.conf   # روی edge
# کلیدها را با `wg genkey | tee private | wg pubkey` بساز و جای‌گذاری کن
systemctl enable --now wg-quick@wg0
wg show          # هر دو طرف باید handshake داشته باشند
```

`WG_CORE_IP` و `WG_EDGE_IP` را در هر دو `.env` بگذار. لیسنر داخلی nginx فقط روی
همین آدرس publish می‌شود، پس پورت ۸۰۸۰ از اینترنت قابل دسترس نیست.

---

## ۳. بالا آوردن هسته

```bash
# فرانت‌اندها را بساز (nginx فایل‌های ساخته‌شده را serve می‌کند)
cd miniapp && npm ci && VITE_API_URL=https://api.example.com npm run build && cd ..
cd admin   && npm ci && VITE_API_URL=https://api.example.com \
                        VITE_BOT_USERNAME=my_bot npm run build && cd ..

docker compose -f infra/compose/core.yml --env-file .env.core up -d
docker compose -f infra/compose/core.yml logs -f migrate   # باید «upgrade head» تمام شود
curl -fsS https://api.example.com/healthz
curl -fsS http://$WG_CORE_IP:8080/readyz
```

اولین owner پنل (عمداً هیچ مسیر خودثبتی وجود ندارد):

```bash
docker compose -f infra/compose/core.yml exec api python -m app.cli add-admin 123456789
# SQL چاپ‌شده را اجرا کن:
docker compose -f infra/compose/core.yml exec postgres psql -U tmusic -d tmusic
```

---

## ۴. بالا آوردن edge

```bash
# لاگین تنها اکانت resolver (اختیاری، فقط یک‌بار؛ سشن رمزنگاری‌شده ذخیره می‌شود).
# بدون آن هم edge بالا می‌آید و کرال می‌کند؛ فقط اولین پخش هر ترک به آن نیاز دارد.
docker compose -f infra/compose/edge.yml run --rm edge python -m tmusic_indexer.login acc1
docker compose -f infra/compose/edge.yml --env-file .env.edge up -d
curl -fsS http://$WG_EDGE_IP:8081/healthz
```

سپس edge را به هسته معرفی کن:

```bash
docker compose -f infra/compose/core.yml exec api python -m app.cli add-edge cdn.example.com 100
```

---

## ۵. وبهوک ربات

```bash
docker compose -f infra/compose/core.yml exec api python -m app.bot.set_webhook
```

در BotFather:
- `/setdomain` → `admin.example.com` (برای Login Widget پنل)
- Mini App URL → `https://app.example.com`

---

## ۶. مانیتورینگ و پشتیبان

Prometheus/Grafana/Alertmanager همراه compose هسته بالا می‌آیند و روی `127.0.0.1`
گوش می‌دهند؛ از بیرون فقط با تونل SSH ببین‌شان:

```bash
ssh -L 3000:127.0.0.1:3000 core-server     # Grafana
```

`infra/monitoring/alertmanager.yml` را با توکن یک **ربات جداگانه** پر کن (اگر ربات
محصول rate limit شود، هشدار باید همچنان برسد) و کرون‌ها را بگذار:

```cron
0 2 * * *  /opt/tmusic/infra/scripts/backup.sh       >> /var/log/tmusic-backup.log 2>&1
0 4 * * 0  /opt/tmusic/infra/scripts/restore-test.sh >> /var/log/tmusic-restore.log 2>&1
```

---

## ۷. تست بار قبل از اعلام آمادگی

```bash
docker compose -f infra/compose/core.yml exec api python -m app.cli fake-initdata 500 \
  > infra/k6/initdata.txt
k6 run -e BASE=https://api.example.com -e VUS=2000 \
       -e INIT_DATA_FILE=infra/k6/initdata.txt infra/k6/load.js
```

اگر آستانه‌ها رد شوند k6 با کد غیرصفر تمام می‌شود. جزئیات در
[`infra/k6/README.md`](../infra/k6/README.md).

---

## ۸. استقرار نسخهٔ جدید

```bash
VERSION=$(git rev-parse --short HEAD)
docker compose -f infra/compose/core.yml --env-file .env.core pull
docker compose -f infra/compose/core.yml --env-file .env.core up -d migrate
docker compose -f infra/compose/core.yml --env-file .env.core up -d api worker nginx
```

- مهاجرت‌ها **قبل** از سرویس اجرا می‌شوند و باید backward-compatible باشند
  (اول ستون اضافه کن، بعد کد، بعد ستون قدیمی را در نسخهٔ بعد حذف کن).
- API بدون حالت است، پس `up -d api` جایگزینی بدون قطعی است.
- برای تغییرات بزرگ: `maintenance_mode` را از پنل روشن کن؛ ترافیک کاربر ۵۰۳ می‌گیرد
  ولی پنل و healthcheck کار می‌کنند.

### برگشت به عقب

```bash
VERSION=<previous-sha> docker compose -f infra/compose/core.yml up -d api worker
```

اگر مهاجرت مقصر است: `alembic downgrade -1` **فقط** وقتی که دستی بررسی کرده باشی
داده از دست نمی‌رود؛ وگرنه از بکاپ بازگردانی کن ([RUNBOOK](RUNBOOK.md#backups)).

---

## ۹. چک‌لیست بعد از استقرار

- [ ] `GET /healthz` و `GET /readyz` سبز
- [ ] `getWebhookInfo` بدون `last_error_message`
- [ ] یک آهنگ در مینی‌اپ پخش می‌شود (یعنی زنجیرهٔ تیکت → edge → Range سالم است)
- [ ] در پنل، صفحهٔ «کرالر» کانال‌های در حال کرال و نرخ استخراج سالم را نشان می‌دهد
- [ ] چند کانال با `python -m app.cli seed-channels channels.txt` وارد شده و وضعیتشان
      از `pending` به `active` رفته است
- [ ] در Grafana: بدون ۵xx، p95 زیر ۲۰۰ms
- [ ] یک پرداخت تستی (sandbox) از ابتدا تا فعال‌شدن اشتراک
