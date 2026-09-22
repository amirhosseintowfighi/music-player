#!/usr/bin/env bash
# One command from a bare server to a running deployment.
#
#   curl -fsSL https://raw.githubusercontent.com/amirhosseintowfighi/music-player/main/scripts/install.sh -o install.sh
#   less install.sh          # read it first — it is short, and you should
#   bash install.sh
#
# Or, if you already cloned the repository, just run it from inside:
#
#   bash scripts/install.sh
#
# It clones (when needed), runs the setup wizard, builds and starts everything,
# waits for the API to answer, and makes you an admin. Every step is a command you
# could have typed yourself; nothing is hidden and nothing is piped from the
# network into a shell.
set -euo pipefail

REPO="${REPO:-https://github.com/amirhosseintowfighi/music-player.git}"
DIR="${DIR:-music-player}"
PROFILE="${PROFILE:-dev}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. what this machine needs ───────────────────────────────────────────────
command -v git >/dev/null || die "git is not installed."
command -v docker >/dev/null || die "docker is not installed — see https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available."
PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || die "python3 is not installed."

# ── 1. the code ──────────────────────────────────────────────────────────────
if [ -f "scripts/setup.py" ]; then
  say "Using the repository in $(pwd)"
else
  if [ -d "$DIR/.git" ]; then
    say "Updating $DIR"
    git -C "$DIR" pull --ff-only
  else
    say "Cloning $REPO"
    git clone --depth 1 "$REPO" "$DIR"
  fi
  cd "$DIR"
fi

# ── 2. configuration (the wizard generates every secret itself) ──────────────
ENV_FILE=".env"
case "$PROFILE" in
  core) ENV_FILE=".env.core" ;;
  edge) ENV_FILE=".env.edge" ;;
esac

if [ -f "$ENV_FILE" ]; then
  say "$ENV_FILE already exists — keeping it (delete it to start over)"
else
  say "Setup wizard"
  "$PYTHON" scripts/setup.py --profile "$PROFILE" --output "$ENV_FILE"
  [ -f "$ENV_FILE" ] || die "The wizard did not write $ENV_FILE."
fi

# ── 3. build and start ───────────────────────────────────────────────────────
case "$PROFILE" in
  core) COMPOSE=(docker compose -f infra/compose/core.yml --env-file "$ENV_FILE") ;;
  edge) COMPOSE=(docker compose -f infra/compose/edge.yml --env-file "$ENV_FILE") ;;
  *)    COMPOSE=(docker compose) ;;
esac

say "Building and starting (this takes a few minutes the first time)"
"${COMPOSE[@]}" up -d --build

if [ "$PROFILE" = "edge" ]; then
  say "Edge is up. Register it on the core:"
  echo "  docker compose -f infra/compose/core.yml --env-file .env.core \\"
  echo "    exec api python -m app.cli add-edge <this-host> 100"
  exit 0
fi

# ── 4. wait until it actually answers ────────────────────────────────────────
say "Waiting for the API"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T api python -c "
import urllib.request, sys
try:
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    printf '  ✓ healthy\n'
    break
  fi
  printf '.'
  sleep 3
done

# ── 5. make the operator an admin ────────────────────────────────────────────
ADMIN_ID="${ADMIN_TG_ID:-$(grep -E '^PAYMENTS_ADMIN_CHAT_ID=' "$ENV_FILE" | cut -d= -f2 | tr -d '"')}"
if [ -n "${ADMIN_ID:-}" ] && [ "$ADMIN_ID" != "0" ]; then
  say "Making $ADMIN_ID an admin"
  "${COMPOSE[@]}" exec -T api python -m app.cli add-admin "$ADMIN_ID" --apply
fi

say "Done."
cat <<'NEXT'
  Next:
    1. register the bot's webhook
         docker compose exec api python -m app.bot.set_webhook
    2. add the first channels to crawl (one username or t.me link per line)
         printf '%s\n' @Musicirani_Official @PersianOldies > channels.txt
         docker compose exec -T api python -m app.cli seed-channels - < channels.txt
    3. open the Mini App from your bot's menu button

  Full guide: docs/GETTING-STARTED.md
NEXT
