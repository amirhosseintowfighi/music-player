#!/usr/bin/env bash
# Ship a new version to a server that install.sh already set up.
#
#   bash scripts/deploy.sh                 # deploy the latest commit of the current branch
#   bash scripts/deploy.sh --ref v1.4.0    # deploy a tag, branch or commit
#   bash scripts/deploy.sh rollback        # go back to the version before the last deploy
#   bash scripts/deploy.sh status          # what is running, and is it healthy
#
# What a deploy does, in order — every step is an ordinary command you could type:
#
#   1. checks the tools, the .env file and that nobody edited tracked files here
#   2. shows what is about to ship (commits, new migrations) and asks once
#   3. dumps the database to $BACKUP_DIR (not on an edge: it has no database)
#   4. moves the checkout to the new version
#   5. builds: the images, and on solo/core the Mini App and the admin panel
#   6. runs the migrations on their own, before any new code serves a request
#   7. starts the new containers and waits for /healthz and /readyz
#   8. re-registers the bot's webhook and command list (/jam and friends)
#
# If anything from step 4 on fails, the checkout and the containers go back to the
# version that was running. Migrations are *not* reversed automatically — they are
# written to be backward compatible (docs/DEPLOY.md §8), and the dump from step 3 is
# there if one ever is not.
#
# Options:
#   --ref <ref>      what to deploy (default: the tip of the current branch on origin)
#   --yes, -y        do not ask for confirmation (for CI or a cron)
#   --no-backup      skip the database dump
#   --no-webhook     do not re-register the webhook
#   --force          deploy even if that version is already running
#
# Environment:
#   PROFILE      dev | solo | core | edge   (guessed from which .env file exists)
#   BACKUP_DIR   where dumps go             (default: /var/backups/tmusic, else ./backups)
#   KEEP_BACKUPS how many pre-deploy dumps to keep (default: 10)
#   REGISTRY     core/edge image registry   (default: the one in the compose file)
set -Eeuo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STATE_DIR="$ROOT/.deploy"
mkdir -p "$STATE_DIR"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── arguments ────────────────────────────────────────────────────────────────
ACTION="deploy"
REF=""
YES=0
BACKUP=1
WEBHOOK=1
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    deploy | rollback | status) ACTION="$1" ;;
    --ref) REF="${2:?--ref needs a value}"; shift ;;
    --ref=*) REF="${1#--ref=}" ;;
    -y | --yes) YES=1 ;;
    --no-backup) BACKUP=0 ;;
    --no-webhook) WEBHOOK=0 ;;
    --force) FORCE=1 ;;
    -h | --help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
  shift
done

# ── which deployment is this ─────────────────────────────────────────────────
if [ -z "${PROFILE:-}" ]; then
  if [ -f .env.core ]; then PROFILE=core
  elif [ -f .env.edge ]; then PROFILE=edge
  elif [ -f .env ] && grep -qE '^DOMAIN=.+' .env; then PROFILE=solo
  else PROFILE=dev
  fi
fi

case "$PROFILE" in
  core) ENV_FILE=.env.core; COMPOSE=(docker compose -f infra/compose/core.yml --env-file .env.core) ;;
  edge) ENV_FILE=.env.edge; COMPOSE=(docker compose -f infra/compose/edge.yml --env-file .env.edge) ;;
  solo) ENV_FILE=.env; COMPOSE=(docker compose -f infra/compose/solo.yml --env-file .env) ;;
  dev) ENV_FILE=.env; COMPOSE=(docker compose) ;;
  *) die "PROFILE must be dev, solo, core or edge (got '$PROFILE')" ;;
esac
HAS_DB=1
[ "$PROFILE" = edge ] && HAS_DB=0

env_value() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' || true; }

# ── checks ───────────────────────────────────────────────────────────────────
command -v git >/dev/null || die "git is not installed."
command -v docker >/dev/null || die "docker is not installed."
docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available."
[ -d .git ] || die "$ROOT is not a git checkout."
[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing — this server was never installed. Run: bash scripts/install.sh"

# Registry images (core/edge) are tagged with the commit they were built from.
compose() { VERSION="$(git rev-parse --short=12 HEAD)" "${COMPOSE[@]}" "$@"; }

# ── health ───────────────────────────────────────────────────────────────────
api_answers() {
  compose exec -T api python -c "
import sys, urllib.request
try:
    for path in ('/healthz', '/readyz'):
        if urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=3).status != 200:
            sys.exit(1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1
}

edge_answers() {
  compose exec -T edge python -c "
import sys, urllib.request
try:
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1
}

wait_healthy() {
  local check=api_answers tries=60
  [ "$HAS_DB" = 0 ] && check=edge_answers
  for _ in $(seq 1 "$tries"); do
    if "$check"; then
      ok "healthy"
      return 0
    fi
    printf '.'
    sleep 3
  done
  echo
  return 1
}

if [ "$ACTION" = status ]; then
  say "Deployment ($PROFILE)"
  printf '  running:  %s\n' "$(cat "$STATE_DIR/current" 2>/dev/null || echo unknown)"
  printf '  checkout: %s  %s\n' "$(git rev-parse --short=12 HEAD)" "$(git log -1 --format=%s)"
  printf '  previous: %s\n' "$(cat "$STATE_DIR/previous" 2>/dev/null || echo none)"
  compose ps
  if [ "$HAS_DB" = 1 ]; then api_answers && ok "API healthy" || warn "API is not answering"
  else edge_answers && ok "edge healthy" || warn "edge is not answering"
  fi
  [ -f "$STATE_DIR/history.log" ] && { say "Last deploys"; tail -n 5 "$STATE_DIR/history.log"; }
  exit 0
fi

# One deploy at a time: two overlapping ones would build and migrate over each other.
exec 9>"$STATE_DIR/lock"
if command -v flock >/dev/null; then
  flock -n 9 || die "Another deploy is running (lock: $STATE_DIR/lock)."
fi

# Local edits would be lost (or would block the checkout); --force does not change that.
if ! git diff --quiet HEAD --; then
  git status --short
  die "Tracked files were edited on this server. Commit, stash or discard them first."
fi

CURRENT="$(git rev-parse HEAD)"
# The branch this server follows. A rollback leaves the checkout detached, so the
# branch is remembered and the next plain deploy goes back to following it.
BRANCH="$(git symbolic-ref --quiet --short HEAD || cat "$STATE_DIR/branch" 2>/dev/null || true)"

# ── what to deploy ───────────────────────────────────────────────────────────
if [ "$ACTION" = rollback ]; then
  TARGET_REF="${REF:-$(cat "$STATE_DIR/previous" 2>/dev/null || true)}"
  [ -n "$TARGET_REF" ] || die "Nothing to roll back to: no earlier deploy is recorded. Use --ref <commit>."
  BACKUP=0
else
  say "Fetching"
  git fetch --tags --prune origin
  if [ -n "$REF" ]; then
    TARGET_REF="$REF"
  elif [ -n "$BRANCH" ]; then
    TARGET_REF="origin/$BRANCH"
  else
    die "The checkout is not on a branch; say what to deploy with --ref."
  fi
fi
TARGET="$(git rev-parse --verify --quiet "$TARGET_REF^{commit}")" || die "Unknown ref: $TARGET_REF"

if [ "$TARGET" = "$CURRENT" ] && [ "$FORCE" = 0 ] && [ -f "$STATE_DIR/current" ] \
  && [ "$(cat "$STATE_DIR/current")" = "$CURRENT" ]; then
  ok "Already running $(git rev-parse --short=12 "$TARGET"). Nothing to do (--force to redeploy)."
  exit 0
fi

say "$ACTION ($PROFILE): $(git rev-parse --short=12 "$CURRENT") → $(git rev-parse --short=12 "$TARGET")"
if git merge-base --is-ancestor "$CURRENT" "$TARGET" 2>/dev/null; then
  git --no-pager log --oneline --no-decorate "$CURRENT..$TARGET" | head -n 30 | sed 's/^/    /'
else
  warn "The target is not ahead of what is running (a rollback, or history was rewritten)."
fi
MIGRATIONS="$(git diff --name-only --diff-filter=A "$CURRENT" "$TARGET" -- backend/migrations/versions/ || true)"
if [ -n "$MIGRATIONS" ] && [ "$HAS_DB" = 1 ]; then
  echo "  new migrations:"
  printf '%s\n' "$MIGRATIONS" | sed 's/^/    /'
fi
if [ "$ACTION" = rollback ] && [ "$HAS_DB" = 1 ]; then
  warn "The database stays on its newer schema. That is safe while migrations are additive."
fi

if [ "$YES" = 0 ]; then
  if [ -t 0 ]; then
    printf '\n  Continue? [y/N] '
    read -r answer
    case "$answer" in y | Y | yes | YES) ;; *) die "Stopped; nothing was changed." ;; esac
  else
    die "Not a terminal: pass --yes to deploy without confirming."
  fi
fi

# ── 3. backup ────────────────────────────────────────────────────────────────
if [ "$BACKUP" = 1 ] && [ "$HAS_DB" = 1 ]; then
  if [ -z "${BACKUP_DIR:-}" ]; then
    if mkdir -p /var/backups/tmusic 2>/dev/null && [ -w /var/backups/tmusic ]; then
      BACKUP_DIR=/var/backups/tmusic
    else
      BACKUP_DIR="$ROOT/backups"
    fi
  fi
  mkdir -p "$BACKUP_DIR"
  DUMP="$BACKUP_DIR/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 "$CURRENT").dump"
  say "Backing up the database → $DUMP"
  if compose ps --status running --services 2>/dev/null | grep -qx postgres; then
    compose exec -T postgres pg_dump -U tmusic -d tmusic -Fc --no-owner --no-privileges >"$DUMP" \
      || { rm -f "$DUMP"; die "The backup failed; nothing was changed."; }
    [ -s "$DUMP" ] || { rm -f "$DUMP"; die "The backup is empty; nothing was changed."; }
    ok "$(du -h "$DUMP" | cut -f1) written"
    # Pre-deploy dumps pile up fast; the daily backup job keeps the long history.
    ls -1t "$BACKUP_DIR"/pre-deploy-*.dump 2>/dev/null | tail -n +"$((${KEEP_BACKUPS:-10} + 1))" | xargs -r rm -f
  else
    warn "postgres is not running, so there is nothing to back up (first start?)."
  fi
fi

# ── from here on, a failure puts the old version back ────────────────────────
ROLLING_BACK=0
restore() {
  local status=$?
  [ "$ROLLING_BACK" = 1 ] && exit "$status"
  ROLLING_BACK=1
  trap - ERR
  printf '\n\033[31m✗ The %s failed (exit %s). Putting %s back.\033[0m\n' \
    "$ACTION" "$status" "$(git rev-parse --short=12 "$CURRENT")" >&2
  checkout "$CURRENT"
  build || warn "Rebuilding the old version failed too — look at the output above."
  compose up -d --remove-orphans || true
  if wait_healthy; then
    warn "Back on $(git rev-parse --short=12 "$CURRENT"). Migrations were not reversed; the dump is ${DUMP:-not taken}."
  else
    warn "The old version is not healthy either. Check: ${COMPOSE[*]} logs --tail=200"
  fi
  printf '%s  %s  FAILED %s → %s\n' "$(date -u +%FT%TZ)" "$ACTION" "$CURRENT" "$TARGET" >>"$STATE_DIR/history.log"
  exit "$status"
}

checkout() {
  local commit="$1"
  if [ -n "$BRANCH" ] && [ "$ACTION" = deploy ] && [ -z "$REF" ]; then
    # Following a branch: move it to the commit (the tree is clean, checked above).
    git checkout --quiet -B "$BRANCH" "$commit"
  else
    git checkout --quiet --detach "$commit"
  fi
}

build_frontends() {
  local domain bot
  domain="$(env_value DOMAIN)"
  bot="$(env_value BOT_USERNAME)"
  [ -n "$domain" ] || die "DOMAIN is not set in $ENV_FILE; the Mini App needs it to find the API."
  say "Building the Mini App and the admin panel"
  if [ "$PROFILE" = solo ]; then
    compose --profile build run --rm frontend
  else
    docker run --rm -v "$ROOT:/src" -w /src \
      -e VITE_API_URL="https://api.$domain" -e VITE_BOT_USERNAME="$bot" node:22-alpine \
      sh -ec "cd /src/miniapp && npm ci --no-audit --no-fund && npm run build && cd /src/admin && npm ci --no-audit --no-fund && npm run build"
  fi
}

# core/edge run registry images tagged with the commit. When CI did not publish one
# for this commit, it is built here, from this checkout, under the same name.
registry_images() {
  local version registry
  version="$(git rev-parse --short=12 HEAD)"
  registry="${REGISTRY:-$(env_value REGISTRY)}"
  registry="${registry:-ghcr.io/example}"
  if compose pull --quiet 2>/dev/null; then
    ok "pulled images for $version"
    return 0
  fi
  warn "No published images for $version; building them here."
  if [ "$PROFILE" = core ]; then
    docker build -f backend/Dockerfile -t "$registry/tmusic-backend:$version" .
  else
    docker build -f indexer/Dockerfile -t "$registry/tmusic-edge:$version" .
  fi
}

build() {
  case "$PROFILE" in
    solo) build_frontends; say "Building images"; compose build ;;
    core) build_frontends; say "Images"; REGISTRY="${REGISTRY:-$(env_value REGISTRY)}" registry_images ;;
    edge) say "Images"; registry_images ;;
    dev) say "Building images"; compose build ;;
  esac
}

trap restore ERR

# ── 4. code ──────────────────────────────────────────────────────────────────
say "Checking out $(git rev-parse --short=12 "$TARGET")"
checkout "$TARGET"

# ── 5. build ─────────────────────────────────────────────────────────────────
build

# ── 6. migrations, before any new code serves a request ──────────────────────
if [ "$HAS_DB" = 1 ] && [ "$ACTION" = deploy ]; then
  say "Migrating the database"
  compose up -d postgres redis >/dev/null
  compose run --rm migrate
  ok "schema is at $(compose run --rm migrate alembic current 2>/dev/null | tail -n 1 || echo '?')"
fi

# ── 7. start and check ───────────────────────────────────────────────────────
say "Starting the new version"
compose up -d --remove-orphans
say "Waiting for it to answer"
wait_healthy || false

# ── 8. the bot ───────────────────────────────────────────────────────────────
if [ "$HAS_DB" = 1 ] && [ "$WEBHOOK" = 1 ] && [ "$PROFILE" != dev ]; then
  say "Registering the webhook and the command list"
  compose exec -T api python -m app.bot.set_webhook || warn "set_webhook failed; the old registration stays in place."
fi

trap - ERR
[ -n "$BRANCH" ] && printf '%s\n' "$BRANCH" >"$STATE_DIR/branch"
printf '%s\n' "$CURRENT" >"$STATE_DIR/previous"
printf '%s\n' "$TARGET" >"$STATE_DIR/current"
printf '%s  %s  ok %s → %s\n' "$(date -u +%FT%TZ)" "$ACTION" "$CURRENT" "$TARGET" >>"$STATE_DIR/history.log"

say "Done: $PROFILE is running $(git rev-parse --short=12 "$TARGET")"
if [ "$ACTION" = rollback ]; then
  echo "  The checkout is pinned here. A plain deploy follows ${BRANCH:+origin/}${BRANCH:-a branch} again —"
  echo "  so fix what broke first, or pin a version with --ref."
else
  echo "  Roll back with:  bash scripts/deploy.sh rollback"
fi
