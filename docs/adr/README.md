# Architecture Decision Records

| # | عنوان | وضعیت |
|---|---|---|
| [0001](0001-backend-python-fastapi.md) | Python 3.12 + FastAPI | پذیرفته |
| [0002](0002-hybrid-topology.md) | توپولوژی هیبرید ایران/خارج | پذیرفته |
| [0003](0003-indexing-mtproto-pool.md) | ایندکس با pool MTProto | **جایگزین‌شده** با ADR-002 |
| [0004](0004-streaming-proxy-no-storage.md) | پروکسی استریم + کش موقت edge | پذیرفته (تأیید کاربر) — ترتیب منبع با 0013 جایگزین شد |
| [0005](0005-track-identity.md) | هویت ترک و dedup دولایه | پذیرفته |
| [0006](0006-search-meilisearch.md) | Meilisearch + pg_trgm | پذیرفته |
| [0007](0007-auth-initdata-jwt.md) | initData → JWT | پذیرفته |
| [0008](0008-payments-provider-port.md) | پورت پرداخت + idempotency | پذیرفته |
| [0009](0009-queue-arq.md) | arq به‌جای Celery | پذیرفته |
| [0010](0010-deploy-compose.md) | Compose در prod، k8s فقط manifest | پذیرفته |
| [0011](0011-recommendations-phase1.md) | item-based CF | پذیرفته |
| [0012](0012-ai-deferred.md) | AI پشت feature flag | پذیرفته |
| [0013](0013-edge-process-and-mtproto-streaming.md) | پروسهٔ edge واحد؛ MTProto مسیر اصلی پخش؛ بدون join | پذیرفته |
| [0014](0014-finglish-consonant-skeleton.md) | فینگلیش با اسکلت صامت + alias | پذیرفته |
| [0015](0015-miniapp-stack.md) | انحراف‌های استک مینی‌اپ (React 19، پل تلگرام اختصاصی) | پذیرفته |
| [0016](0016-payments-implementation.md) | جزئیات پیاده‌سازی پرداخت (فاز ۶) | پذیرفته |
| [0017](0017-cf-in-sql.md) | ماتریس شباهت در SQL به‌جای numpy/scipy | پذیرفته |
| [0018](0018-admin-auth-and-panel.md) | احراز هویت پنل ادمین و مرز آن با اپ کاربر | پذیرفته |
| [0019](0019-social-notifications-id3.md) | شبکهٔ اجتماعی، اعلان‌ها و خواندن تگ ID3 | پذیرفته |
| [ADR-002](../ADR-002-indexing-strategy.md) | کاتالوگ از وب‌پیش‌نمایش؛ MTProto فقط fallback | **پذیرفته و اجراشده** — جایگزین 0003 |
| [ADR-003](../ADR-003-playback-and-metadata.md) | پخش، پلیر و کیفیت متادیتا (فازهای ۱۰ تا ۱۳) | **پیشنهادی** — منتظر تأیید |

قالب: زمینه → تصمیم → گزینه‌های ردشده → پیامدها. ADR پذیرفته‌شده ویرایش نمی‌شود؛ یک ADR جدید جایگزینش می‌شود.
