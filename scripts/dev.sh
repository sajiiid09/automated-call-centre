#!/usr/bin/env bash
#
# Bring the whole stack up in tmux, one service per window.
#
# The ordering here is load-bearing, not stylistic:
#
#   cloudflared  ->  .env  ->  Twilio  ->  backend
#
# `app/config.py` reads .env once at import, so a backend started before the
# tunnel exists holds a stale PUBLIC_BASE_URL for its whole life. And the
# trycloudflare hostname changes on every restart, so .env, the Twilio number's
# webhooks, and the running backend must all be rewritten together — miss the
# Twilio side and every inbound call 403s on the signature check, silently.
#
# Usage:  ./scripts/dev.sh [--force] [--no-attach]
# Stop:   ./scripts/stop.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="acc"
BACKEND_PORT=8000
FRONTEND_PORT=3000
LOG_DIR="$REPO_ROOT/.dev"
TUNNEL_LOG="$LOG_DIR/cloudflared.log"
TUNNEL_WAIT_SECONDS=60
DB_WAIT_SECONDS=60
# Postgres lives on the `default` docker context. The active context is Docker
# Desktop, which has no acc-db — omitting this silently builds a second, empty
# database and every query goes to the wrong place.
DOCKER_CTX="default"

FORCE=0
ATTACH=1
for arg in "$@"; do
  case "$arg" in
    --force)      FORCE=1 ;;
    --no-attach)  ATTACH=0 ;;
    -h|--help)    sed -n '3,17p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *)            echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warn\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m fail\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight ------------------------------------------------------------
# Everything that can be known before we start processes is checked here, so a
# failure never leaves half a stack running.

for bin in tmux cloudflared docker curl python3; do
  command -v "$bin" >/dev/null || die "$bin is not installed"
done

[ -f "$REPO_ROOT/.env" ]                   || die ".env is missing (copy .env.example and fill it in)"
[ -x "$REPO_ROOT/backend/.venv/bin/python" ] || die "backend/.venv is missing — see AGENTS.md 'Install'"
[ -d "$REPO_ROOT/frontend/node_modules" ]  || die "frontend/node_modules is missing — run: cd frontend && npm install"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  [ "$FORCE" -eq 1 ] || die "tmux session '$SESSION' is already running. Stop it with ./scripts/stop.sh, or pass --force"
  say "killing existing '$SESSION' session (--force)"
  tmux kill-session -t "$SESSION"
  sleep 1
fi

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  ! port_busy "$port" || die "port $port is already in use — stop whatever holds it first (./scripts/stop.sh)"
done

mkdir -p "$LOG_DIR"
: > "$TUNNEL_LOG"

# --- 1. postgres ----------------------------------------------------------

say "starting Postgres (docker context: $DOCKER_CTX)"
docker --context "$DOCKER_CTX" compose --project-directory "$REPO_ROOT" up -d db >/dev/null

for _ in $(seq "$DB_WAIT_SECONDS"); do
  status="$(docker --context "$DOCKER_CTX" inspect --format '{{.State.Health.Status}}' acc-db 2>/dev/null || true)"
  [ "$status" = "healthy" ] && break
  sleep 1
done
[ "${status:-}" = "healthy" ] || die "Postgres did not become healthy in ${DB_WAIT_SECONDS}s (docker --context $DOCKER_CTX compose logs db)"
say "Postgres healthy on localhost:5433"

# --- 2. tunnel ------------------------------------------------------------
# tee, so the window shows live logs *and* this script can read the URL back.

say "starting cloudflared tunnel"
tmux new-session -d -s "$SESSION" -n tunnel -c "$REPO_ROOT" \
  "cloudflared tunnel --url http://localhost:$BACKEND_PORT --no-autoupdate 2>&1 | tee -a '$TUNNEL_LOG'"

PUBLIC_URL=""
for _ in $(seq "$TUNNEL_WAIT_SECONDS"); do
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)"
  [ -n "$PUBLIC_URL" ] && break
  sleep 1
done

if [ -z "$PUBLIC_URL" ]; then
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  die "no tunnel URL after ${TUNNEL_WAIT_SECONDS}s — see $TUNNEL_LOG"
fi
say "tunnel: $PUBLIC_URL"

# --- 3. .env --------------------------------------------------------------
# Rewrites exactly one line. .env is otherwise off-limits (AGENTS.md); this is
# the deliberate exception, and no secret is read, logged, or written.

say "updating PUBLIC_BASE_URL in .env"
PUBLIC_URL="$PUBLIC_URL" python3 - "$REPO_ROOT/.env" <<'PY'
import os, sys, re

path, url = sys.argv[1], os.environ["PUBLIC_URL"]
with open(path) as fh:
    lines = fh.readlines()

replaced = False
for i, line in enumerate(lines):
    if re.match(r"\s*PUBLIC_BASE_URL\s*=", line):
        lines[i] = f"PUBLIC_BASE_URL={url}\n"
        replaced = True
        break
if not replaced:
    lines.append(f"\nPUBLIC_BASE_URL={url}\n")

with open(path, "w") as fh:
    fh.writelines(lines)
PY

# --- 4. twilio ------------------------------------------------------------
# Read credentials only after .env is final. `set -a` would export every key in
# the file, so the values are pulled out one at a time instead.

env_get() { python3 -c "
import re,sys
for line in open('$REPO_ROOT/.env'):
    m = re.match(r'\s*$1\s*=\s*(.*)', line)
    if m:
        print(m.group(1).split('#')[0].strip()); break
"; }

TW_SID="$(env_get TWILIO_ACCOUNT_SID)"
TW_TOKEN="$(env_get TWILIO_AUTH_TOKEN)"
TW_NUMBER="$(env_get TWILIO_PHONE_NUMBER)"
VOICE_URL="$PUBLIC_URL/twilio/inbound"
STATUS_URL="$PUBLIC_URL/twilio/status"

if [ -n "$TW_SID" ] && [ -n "$TW_TOKEN" ] && [ -n "$TW_NUMBER" ]; then
  say "repointing Twilio number $TW_NUMBER at the new tunnel"
  API="https://api.twilio.com/2010-04-01/Accounts/$TW_SID"
  NUMBER_SID="$(curl -fsS -u "$TW_SID:$TW_TOKEN" "$API/IncomingPhoneNumbers.json" \
    | TW_NUMBER="$TW_NUMBER" python3 -c "
import json, os, sys
want = os.environ['TW_NUMBER']
for n in json.load(sys.stdin).get('incoming_phone_numbers', []):
    if n['phone_number'] == want:
        print(n['sid']); break
" || true)"

  if [ -z "$NUMBER_SID" ]; then
    warn "$TW_NUMBER not found on this Twilio account — skipping. Inbound calls will not reach this backend."
  else
    curl -fsS -u "$TW_SID:$TW_TOKEN" -X POST "$API/IncomingPhoneNumbers/$NUMBER_SID.json" \
      --data-urlencode "VoiceUrl=$VOICE_URL" \
      --data-urlencode "VoiceMethod=POST" \
      --data-urlencode "StatusCallback=$STATUS_URL" \
      --data-urlencode "StatusCallbackMethod=POST" >/dev/null \
      && say "Twilio updated" \
      || warn "Twilio update failed — inbound calls will 403 or hit the old URL"

    # read back rather than trusting the write
    curl -fsS -u "$TW_SID:$TW_TOKEN" "$API/IncomingPhoneNumbers/$NUMBER_SID.json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('      voice_url      :', d.get('voice_url'))
print('      status_callback:', d.get('status_callback'))
" || true
  fi
else
  warn "Twilio credentials incomplete in .env — skipping webhook update (browser web-calls still work)"
fi

# --- 5. app processes -----------------------------------------------------
# Backend starts only now, so it imports the fresh PUBLIC_BASE_URL.
# One uvicorn worker on purpose: campaign_runner starts a dial supervisor per
# process, and N workers means N supervisors (AGENTS.md).

say "starting backend on :$BACKEND_PORT"
tmux new-window -t "$SESSION" -n backend -c "$REPO_ROOT/backend" \
  "source .venv/bin/activate && uvicorn app.main:app --reload --port $BACKEND_PORT; exec \$SHELL"

say "starting frontend on :$FRONTEND_PORT"
tmux new-window -t "$SESSION" -n frontend -c "$REPO_ROOT/frontend" \
  "npm run dev; exec \$SHELL"

# QA shell: venv live, tunnel URL exported, common probes printed once.
tmux new-window -t "$SESSION" -n qa -c "$REPO_ROOT" \
  "source backend/.venv/bin/activate; export PUBLIC_BASE_URL='$PUBLIC_URL'; \
   printf '\nQA shell — venv active, \$PUBLIC_BASE_URL set\n\n  curl -s localhost:$BACKEND_PORT/health\n  curl -s \$PUBLIC_BASE_URL/health\n  curl -s -o /dev/null -w %%{http_code} -X POST \$PUBLIC_BASE_URL/twilio/status -d CallSid=CA1   # expect 403\n  (cd backend && pytest -q)\n  docker --context $DOCKER_CTX compose exec -T db psql -U acc -d callcentre\n\n'; \
   exec \$SHELL"

# --- 6. wait for the app to answer ---------------------------------------

for _ in $(seq 45); do
  curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS --max-time 5 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1; then
  say "backend healthy"
else
  warn "backend has not answered /health yet — check the 'backend' window"
fi

tmux select-window -t "$SESSION:backend"

cat <<EOF

  ────────────────────────────────────────────────────────────────
   Stack is up in tmux session '$SESSION'

     tunnel     $PUBLIC_URL
     backend    http://localhost:$BACKEND_PORT
     frontend   http://localhost:$FRONTEND_PORT
     postgres   localhost:5433  (docker context: $DOCKER_CTX)

     Twilio voice     $VOICE_URL
     Twilio status    $STATUS_URL

   Windows:  0 tunnel   1 backend   2 frontend   3 qa
     switch      Ctrl-b 0 / 1 / 2 / 3      (Ctrl-b w for the picker)
     detach      Ctrl-b d                  (leaves everything running)
     reattach    tmux attach -t $SESSION

   To stop:
     ./scripts/stop.sh          tunnel + backend + frontend
     ./scripts/stop.sh --db     the above, and Postgres too

   Note: the tunnel hostname changes every run, so re-running this script
   is the supported way to restart — it rewires .env and Twilio for you.
  ────────────────────────────────────────────────────────────────

EOF

if [ "$ATTACH" -eq 1 ]; then
  tmux attach -t "$SESSION"
fi
