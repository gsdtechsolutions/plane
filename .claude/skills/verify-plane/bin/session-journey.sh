#!/usr/bin/env bash
# Session journey over HTTP, the same endpoints the sign-in form and the app call.
#   session-journey.sh            # against the local stack (adds DB expiry checks)
#   REMOTE_BASE=https://plane.gsdut.dev EMAIL=... PASSWORD=... session-journey.sh   # remote, HTTP checks only
# Pass: sign-in cookie lives >= MIN_AGE, an authenticated GET re-issues it, the DB expiry
# moves forward on activity (local), and sign-out ends the session.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
eval "$("$HERE/stack.sh" env)"   # BASE, PROJECT, STATE, EVIDENCE for the local stack
LOCAL=1; [ -n "${REMOTE_BASE:-}" ] && { BASE=$REMOTE_BASE; LOCAL=0; }
EMAIL=${EMAIL:-${SEED_EMAIL:-verify-user@example.com}}
PASSWORD=${PASSWORD:-${SEED_PASSWORD:-Verify-Plane-2026!}}
MIN_AGE=${MIN_AGE:-2592000}
GAP=${GAP:-3}
OUT=$EVIDENCE/$(date -u +%Y%m%dT%H%M%SZ)-session-journey; mkdir -p "$OUT"
jar=$OUT/cookies.txt; log=$OUT/transcript.txt
fail=0; say() { echo "$*" | tee -a "$log"; }; bad() { say "FAIL: $*"; fail=1; }
cookie_age() { grep -i '^set-cookie: session-id=' "$1" | grep -oi 'max-age=[0-9]*' | cut -d= -f2 || true; }
db_expiry() { "$HERE/stack.sh" psql "select extract(epoch from expire_date)::bigint from sessions where session_key='$1'"; }

say "target: $BASE  user: $EMAIL  local-db-checks: $LOCAL"
token=$(curl -sf -c "$jar" -b "$jar" "$BASE/auth/get-csrf-token/" | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
code=$(curl -s -c "$jar" -b "$jar" -D "$OUT/signin.headers" -o /dev/null -w '%{http_code} %{redirect_url}' -X POST "$BASE/auth/sign-in/" \
  -H "Referer: $BASE/" -H "Origin: $BASE" --data-urlencode "csrfmiddlewaretoken=$token" \
  --data-urlencode "email=$EMAIL" --data-urlencode "password=$PASSWORD")
say "POST /auth/sign-in/: $code"
age=$(cookie_age "$OUT/signin.headers"); say "sign-in session-id Max-Age: ${age:-none}"
[ "${age:-0}" -ge "$MIN_AGE" ] || bad "sign-in Max-Age below $MIN_AGE"
key=$(awk '$6=="session-id"{print $7}' "$jar" | tail -1)
[ -n "$key" ] || { bad "no session-id cookie after sign-in"; exit 1; }
[ $LOCAL = 1 ] && { e1=$(db_expiry "$key"); say "db expire_date after sign-in: $e1"; }

sleep "$GAP"
code=$(curl -s -c "$jar" -b "$jar" -D "$OUT/me.headers" -o "$OUT/me.json" -w '%{http_code}' "$BASE/api/users/me/")
age=$(cookie_age "$OUT/me.headers")
say "GET /api/users/me/ after ${GAP}s: $code, re-issued Max-Age: ${age:-none}"
[ "$code" = 200 ] || bad "/api/users/me/ returned $code"
[ -n "$age" ] || bad "authenticated request did not extend the session"
if [ $LOCAL = 1 ]; then
  e2=$(db_expiry "$key"); say "db expire_date after activity: $e2 (moved $((e2 - e1))s)"
  [ "$e2" -gt "$e1" ] || bad "db expiry did not move forward"
fi

token=$(curl -sf -c "$jar" -b "$jar" "$BASE/auth/get-csrf-token/" | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
curl -s -c "$jar" -b "$jar" -o /dev/null -X POST "$BASE/auth/sign-out/" -H "Referer: $BASE/" -H "Origin: $BASE" --data-urlencode "csrfmiddlewaretoken=$token"
code=$(curl -s -b "$jar" -o /dev/null -w '%{http_code}' "$BASE/api/users/me/")
say "GET /api/users/me/ after sign-out: $code"
[ "$code" = 401 ] || bad "session still valid after sign-out"
if [ $LOCAL = 1 ]; then
  n=$("$HERE/stack.sh" psql "select count(*) from sessions where session_key='$key'"); say "session rows after sign-out: $n"
  [ "$n" = 0 ] || bad "session row survived sign-out"
fi
rm -f "$jar"   # the evidence keeps headers, not live session keys
[ $fail = 0 ] && say "PASS" || say "RESULT: FAIL"
say "evidence: $OUT"
exit $fail
