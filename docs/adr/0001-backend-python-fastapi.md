# ADR-0001: Python 3.12 + FastAPI برای کل بک‌اند

- وضعیت: پذیرفته (تأیید کاربر، ۲۰۲۶-۰۹-۱۶)

## زمینه
سه لایه داریم: API، بات، ایندکسر MTProto. ریسکی‌ترین لایه ایندکسر است.

## تصمیم
Python 3.12 · FastAPI · aiogram 3 · Telethon · SQLAlchemy 2 (async) + asyncpg · Alembic · Pydantic v2 · mypy --strict.

## پیامدها
- Telethon بالغ‌ترین کتابخانهٔ MTProto است؛ مدیریت FloodWait و session در آن جاافتاده است.
- یک زبان برای API/بات/ورکر/ایندکسر → مدل‌های مشترک، یک CI.
- فرانت TypeScript است؛ قرارداد دو طرف با OpenAPI تولیدشده از FastAPI و `openapi-typescript` تضمین می‌شود، نه با تایپ دستی.
