#!/usr/bin/env bash
#
# Tear down what scripts/dev.sh started.
#
# Killing the tmux session takes the tunnel, the backend and the frontend with
# it — they are all children of its windows. Postgres is a detached container,
# so it survives unless you ask for it.
#
# Usage:  ./scripts/stop.sh [--db]
#           --db   also stop the Postgres container
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="acc"
DOCKER_CTX="default"

STOP_DB=0
for arg in "$@"; do
  case "$arg" in
    --db)       STOP_DB=1 ;;
    -h|--help)  sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *)          echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
  say "stopped tmux session '$SESSION' (tunnel, backend, frontend)"
else
  say "no tmux session '$SESSION' was running"
fi

# The tunnel is the one process worth chasing: an orphan keeps the old hostname
# alive, so a stale PUBLIC_BASE_URL still answers and looks correct while Twilio
# points somewhere else entirely.
#
# kill-session returns before its children are reaped, and a tunnel started by
# an earlier run (or by hand) is not a child at all — so poll rather than check
# once, and escalate to SIGKILL if it will not go.
TUNNEL_PATTERN="cloudflared tunnel --url http://localhost:8000"
if pgrep -f "$TUNNEL_PATTERN" >/dev/null 2>&1; then
  pkill -f "$TUNNEL_PATTERN" 2>/dev/null || true
  for _ in $(seq 10); do
    pgrep -f "$TUNNEL_PATTERN" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if pgrep -f "$TUNNEL_PATTERN" >/dev/null 2>&1; then
    pkill -9 -f "$TUNNEL_PATTERN" 2>/dev/null || true
    sleep 0.5
  fi
  pgrep -f "$TUNNEL_PATTERN" >/dev/null 2>&1 \
    && say "WARNING: a cloudflared tunnel is still running — kill it by hand" \
    || say "stopped cloudflared tunnel"
fi

if [ "$STOP_DB" -eq 1 ]; then
  if command -v docker >/dev/null; then
    docker --context "$DOCKER_CTX" compose --project-directory "$REPO_ROOT" stop db >/dev/null 2>&1 || true
    say "stopped Postgres"
  fi
else
  say "Postgres left running — use --db to stop it too"
fi

printf '\n  Start again with: ./scripts/dev.sh\n\n'
