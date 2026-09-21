# ADR-0010: استقرار با Docker Compose؛ manifest های Kubernetes تحویل می‌شوند ولی اجرا نمی‌شوند

- وضعیت: پذیرفته

## زمینه
بودجه ۵۰–۲۰۰ دلار است؛ یک ماشین core و یک یا دو edge داریم.

## تصمیم
- dev و prod هر دو با Docker Compose (`compose.core.yml`، `compose.edge.yml`).
- استقرار با GitHub Actions + SSH + `docker compose pull && up -d`، همراه healthcheck و rollback خودکار.
- manifest های Kubernetes طبق بریف در `infra/k8s/` تحویل و در CI با `kubeconform` اعتبارسنجی می‌شوند، ولی فعلاً مسیر production نیستند.

## دلیل
کنترل‌پلین k8s روی یک یا دو ماشین سهم بزرگی از RAM و بودجه را می‌گیرد و در این مقیاس سودی ندارد. همهٔ سرویس‌ها stateless هستند، پس مهاجرت بعدی کار مکانیکی است.

## آستانهٔ بازنگری
بیش از ۳ ماشین در core، یا نیاز به autoscaling.
