# Kubernetes manifests

طبق [ADR-0010](../../docs/adr/0010-deploy-compose.md) مسیر production فعلی **Docker
Compose** است؛ این manifestها تحویل داده می‌شوند تا مهاجرت بعدی کار مکانیکی باشد و در
CI با `kubeconform` اعتبارسنجی می‌شوند، ولی امروز روی کلاستر اجرا نمی‌شوند.

```
infra/k8s/
  base/            # هستهٔ بدون‌حالت: api، worker، ingress، config
  edge/            # سرویس edge (ایندکسر + استریمر) که خارج از ایران اجرا می‌شود
```

## پیش‌فرض‌ها

- دیتابیس، Redis و Meilisearch **مدیریت‌شده یا خارج از کلاستر** هستند. اجرای Postgres
  با state روی همان کلاستری که اپ را می‌برد، برای این مقیاس ریسک بی‌دلیل است.
- Secretها از یک SecretStore بیرونی (یا `kubectl create secret`) می‌آیند؛ هیچ مقدار
  محرمانه‌ای در این فایل‌ها نیست — همان قاعدهٔ §۰ بریف.
- `edge` روی نودهای خارج از ایران با `nodeSelector` و PVC برای کش صوت اجرا می‌شود.

## اجرا

```bash
kubectl create namespace tmusic
kubectl -n tmusic create secret generic tmusic-secrets \
  --from-env-file=.env.core           # فقط کلیدها؛ بقیه در ConfigMap است
kubectl -n tmusic apply -k base/
kubectl -n tmusic apply -k edge/      # روی کلاستر خارج
```

## اعتبارسنجی

```bash
kubeconform -strict -summary -ignore-missing-schemas infra/k8s/base infra/k8s/edge
```
