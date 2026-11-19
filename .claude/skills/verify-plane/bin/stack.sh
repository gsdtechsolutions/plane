#!/usr/bin/env bash
# Run this checkout's Coolify deploy compose (deployments/coolify/compose.yml) as an
# isolated local stack: own compose project, own images, own port, no Traefik routing.
#
#   stack.sh build | up | doctor | seed | psql [SQL] | logs [svc] | env | down
#
# Env: PLANE_CHECKOUT (default: git toplevel of $PWD), PLANE_VERIFY_PROJECT (plane-verify),
#      PLANE_VERIFY_PORT (38080), PLANE_VERIFY_HOME (~/.local/state/plane-verify).
set -euo pipefail

CHECKOUT=$(cd "${PLANE_CHECKOUT:-$(git rev-parse --show-toplevel)}" && pwd)
PROJECT=${PLANE_VERIFY_PROJECT:-plane-verify}
PORT=${PLANE_VERIFY_PORT:-38080}
VHOME=${PLANE_VERIFY_HOME:-$HOME/.local/state/plane-verify}
STATE=$VHOME/$PROJECT
BASE=http://localhost:$PORT
COMPOSE_FILE=$CHECKOUT/deployments/coolify/compose.yml
SEED_EMAIL=${SEED_EMAIL:-verify-user@example.com}
SEED_ADMIN_EMAIL=${SEED_ADMIN_EMAIL:-verify-admin@example.com}
SEED_PASSWORD=${SEED_PASSWORD:-Verify-Plane-2026!}
SEED_WORKSPACE=${SEED_WORKSPACE:-verify}

die() { echo "stack.sh: $*" >&2; exit 1; }
rand() { python3 -c 'import secrets;print(secrets.token_hex(24))'; }

write_state() {
  mkdir -p "$STATE"
  if [ ! -f "$STATE/.env" ]; then
    local pg; pg=$(rand)
    umask 077
    cat > "$STATE/.env" <<EOF
SECRET_KEY=$(rand)
LIVE_SERVER_SECRET_KEY=$(rand)
POSTGRES_PASSWORD=$pg
DATABASE_URL=postgresql://plane:$pg@plane-db:5432/plane
AWS_ACCESS_KEY_ID=$(rand)
AWS_SECRET_ACCESS_KEY=$(rand)
APP_DOMAIN=localhost
WEB_URL=$BASE
APP_BASE_URL=$BASE
CORS_ALLOWED_ORIGINS=$BASE
GITHUB_APP_BASE_URL=$BASE
SLACK_APP_BASE_URL=$BASE
GUNICORN_WORKERS=2
MINIO_ENDPOINT_SSL=0
# Same value Coolify stores for production.
SESSION_DURATION=2592000
EOF
  fi
  # Per-project image names so two checkouts never overwrite each other's builds.
  # The proxy loses its Traefik labels and the external coolify network: this host
  # runs Coolify too, and those labels would route plane.gsdut.dev to this stack.
  # Production's origin sits behind Cloudflare's asset cache, so its per-IP static limit
  # (300/min) rarely sees a full page load. Here every request comes from one IP and one
  # cold workspace load is ~400 requests, so the local web Caddy gets a higher limit.
  sed 's/^\([[:space:]]*events\) 300$/\1 5000/' "$CHECKOUT/apps/web/caddy/Caddyfile" > "$STATE/web.Caddyfile"
  grep -q 'events 5000' "$STATE/web.Caddyfile" || die "web Caddyfile rate limit not found; update stack.sh"
  cat > "$STATE/override.yml" <<EOF
services:
  api: {image: $PROJECT-backend:local}
  worker: {image: $PROJECT-backend:local}
  beat-worker: {image: $PROJECT-backend:local}
  migrator: {image: $PROJECT-backend:local}
  web:
    image: $PROJECT-web:local
    volumes: ["$STATE/web.Caddyfile:/etc/caddy/Caddyfile:ro"]
  space: {image: $PROJECT-space:local}
  admin: {image: $PROJECT-admin:local}
  live: {image: $PROJECT-live:local}
  proxy:
    image: $PROJECT-proxy:local
    labels: !reset []
    networks: !override [default]
    ports: ["127.0.0.1:$PORT:80"]
networks:
  coolify: !reset {}
EOF
}

compose() {
  docker compose -p "$PROJECT" --project-directory "$CHECKOUT" --env-file "$STATE/.env" \
    -f "$COMPOSE_FILE" -f "$STATE/override.yml" "$@"
}

api_py() { compose exec -T api python manage.py shell -c "$1"; }

doctor() {
  local ok=0
  echo "checkout: $CHECKOUT ($(git -C "$CHECKOUT" rev-parse --short HEAD))"
  [ -f "$STATE/built-commit" ] && echo "images built from: $(cat "$STATE/built-commit")"
  [ -f "$STATE/built-commit" ] && [ "$(cat "$STATE/built-commit")" != "$(git -C "$CHECKOUT" rev-parse --short HEAD)" ] \
    && { echo "WARN: images are older than the checkout; run stack.sh build"; ok=1; }
  compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' | sort | while read -r svc st code; do
    case "$svc:$st" in migrator:exited) [ "$code" = 0 ] || echo "FAIL: migrator exited $code" ;;
      *:running) ;; *) echo "FAIL: $svc is $st" ;; esac
  done | tee "$STATE/doctor.svc" ; [ -s "$STATE/doctor.svc" ] && ok=1
  local owner; owner=$(docker ps --filter "publish=$PORT" --format '{{.Names}}')
  case "$owner" in "$PROJECT"-proxy-*) echo "port $PORT: $owner" ;; *) echo "FAIL: port $PORT owned by '${owner:-nothing}'"; ok=1 ;; esac
  local code; code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/instances/")
  echo "GET /api/instances/: $code"; [ "$code" = 200 ] || ok=1
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")
  echo "GET /: $code"; [ "$code" = 200 ] || ok=1
  echo -n "api session settings: "
  api_py 'from django.conf import settings as s; print("SESSION_COOKIE_AGE", s.SESSION_COOKIE_AGE, "SESSION_SAVE_EVERY_REQUEST", s.SESSION_SAVE_EVERY_REQUEST)'
  [ $ok = 0 ] && echo "DOCTOR OK" || { echo "DOCTOR FAIL"; return 1; }
}

wait_ready() {
  for _ in $(seq 1 90); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/instances/")" = 200 ] \
      && [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" = 200 ] && return 0
    sleep 5
  done
  die "stack not ready after 450s; run stack.sh logs api"
}

seed() {
  # Instance admin goes through the real god-mode sign-up endpoint; the regular user,
  # its onboarding flags, and a workspace are created in Django so journeys start signed-out.
  local jar; jar=$(mktemp); trap 'rm -f "$jar"' RETURN
  local token; token=$(curl -sf -c "$jar" -b "$jar" "$BASE/auth/get-csrf-token/" | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
  curl -s -o /dev/null -c "$jar" -b "$jar" -X POST "$BASE/api/instances/admins/sign-up/" -H "Referer: $BASE/god-mode/" \
    --data-urlencode "csrfmiddlewaretoken=$token" --data-urlencode "email=$SEED_ADMIN_EMAIL" \
    --data-urlencode "first_name=Verify" --data-urlencode "company_name=plane-verify" \
    --data-urlencode "password=$SEED_PASSWORD" --data-urlencode "is_telemetry_enabled=False"
  api_py "
from plane.db.models import User, Profile, Workspace, WorkspaceMember
from plane.license.models import Instance
assert Instance.objects.filter(is_setup_done=True).exists(), 'instance admin sign-up failed'
u, created = User.objects.get_or_create(email='$SEED_EMAIL', defaults={'username': 'verify-user', 'first_name': 'Verify', 'last_name': 'User', 'is_password_autoset': False})
if created:
    u.set_password('$SEED_PASSWORD'); u.save()
ws, _ = Workspace.objects.get_or_create(slug='$SEED_WORKSPACE', defaults={'name': 'Verify', 'owner': u})
WorkspaceMember.objects.get_or_create(workspace=ws, member=u, defaults={'role': 20})
p, _ = Profile.objects.get_or_create(user=u)
p.is_onboarded = True; p.is_tour_completed = True; p.last_workspace_id = ws.id
p.onboarding_step = {'profile_complete': True, 'workspace_create': True, 'workspace_invite': True, 'workspace_join': True}
p.save()
print('seeded', u.email, 'workspace', ws.slug)
"
}

cmd=${1:-}; shift || true
[ -f "$COMPOSE_FILE" ] || die "no $COMPOSE_FILE in $CHECKOUT"
write_state
case "$cmd" in
  # api, worker, beat-worker and migrator share one image; building all four races on its tag.
  build) [ $# -gt 0 ] || set -- api web space admin live proxy
    compose build "$@" && git -C "$CHECKOUT" rev-parse --short HEAD > "$STATE/built-commit" ;;
  up) compose up -d "$@" && wait_ready && echo "READY $BASE" ;;
  doctor) doctor ;;
  seed) seed ;;
  # The socket, not $PGHOST: the db env points PGHOST at the TCP service, which needs a password.
  psql) compose exec -T plane-db psql -h /var/run/postgresql -U plane -d plane -tA ${1:+-c "$1"} ;;
  logs) compose logs --tail 200 "$@" ;;
  env) echo "BASE=$BASE PROJECT=$PROJECT STATE=$STATE EVIDENCE=$VHOME/evidence" ;;
  down) compose down -v --remove-orphans && rm -f "$STATE/.env" "$STATE/doctor.svc" ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
