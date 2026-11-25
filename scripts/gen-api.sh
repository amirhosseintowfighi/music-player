#!/usr/bin/env bash
# Regenerates docs/openapi.json from the backend and the TypeScript client types of
# all three frontends from it. Needs the backend installed (see README) and npm ci
# done in miniapp/, web/ and admin/.
set -euo pipefail
cd "$(dirname "$0")/.."
(
  cd backend
  export BOT_TOKEN="${BOT_TOKEN:-1:x}" BOT_USERNAME="${BOT_USERNAME:-b}" WEBAPP_URL="${WEBAPP_URL:-https://x}" \
    WEBHOOK_SECRET="${WEBHOOK_SECRET:-s}" JWT_PRIVATE_KEY="${JWT_PRIVATE_KEY:-x}" JWT_PUBLIC_KEY="${JWT_PUBLIC_KEY:-x}" \
    STREAM_SIGNING_KEYS="${STREAM_SIGNING_KEYS:-dGVzdC1zaWduaW5nLWtleS0xMjM0NTY3ODkwMTIzNA}" \
    INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-t}" DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://x@localhost/x}" \
    MEILI_API_KEY="${MEILI_API_KEY:-x}"
  python -c "import json; from app.api.main import create_app; open('../docs/openapi.json','w',encoding='utf-8').write(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))"
)
for app in miniapp web admin; do
  (cd "$app" && npx --no-install openapi-typescript ../docs/openapi.json -o src/api/schema.d.ts >/dev/null)
done
echo "openapi.json and schema.d.ts regenerated"
