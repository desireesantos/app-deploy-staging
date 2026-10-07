#!/usr/bin/env bash
# Deploys the app on the staging server. CI pipes this file over SSH:
#   ssh user@host "bash -ls -- '/path/to/app'" < scripts/deploy_staging.sh
# It can also be run by hand on the server:
#   bash scripts/deploy_staging.sh /path/to/app
#
# All the work happens in main(), called on the last line, so bash has read
# the whole script before anything runs. A command that reads stdin can't
# swallow the rest of it.
set -euo pipefail

HEALTH_URL="http://localhost:9100/health"
HEALTH_ATTEMPTS=15
HEALTH_INTERVAL=2
STOP_TIMEOUT=30

# Replaces a detached byobu session so the process outlives the SSH session.
# The old process gets TERM (a Celery warm shutdown) and up to STOP_TIMEOUT
# seconds to exit first: closing the session alone sends HUP, which Celery
# treats as "restart", leaving an orphaned worker. On a first deploy the
# session doesn't exist yet, so there is nothing to stop.
# The command is passed as separate arguments and exec'd, so the pane's
# process is the app itself, and the pane stays open if it exits, so
# `byobu attach -t <name>` shows why.
restart_session() {
  local name="$1" command="$2" pid _
  if pid=$(byobu list-panes -t "$name" -F '#{pane_pid}' 2>/dev/null); then
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 "$STOP_TIMEOUT"); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
  fi
  byobu kill-session -t "$name" 2>/dev/null || true
  byobu new-session -d -s "$name" -c "$PWD" bash -lc "exec $command"
  byobu set-option -t "$name" remain-on-exit on
}

check_session_alive() {
  local name="$1"
  if [ "$(byobu list-panes -t "$name" -F '#{pane_dead}' 2>/dev/null)" != "0" ]; then
    echo "$name is not running. Inspect it with: byobu attach -t $name" >&2
    return 1
  fi
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 "$HEALTH_ATTEMPTS"); do
    if curl -fsS --max-time 5 -o /dev/null "$HEALTH_URL"; then
      echo "Health check passed on attempt $attempt."
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
  done
  echo "Health check failed: $HEALTH_URL did not answer after $HEALTH_ATTEMPTS attempts." >&2
  echo "Inspect the server with: byobu attach -t web" >&2
  return 1
}

main() {
  if [ "$#" -ne 1 ] || [ -z "$1" ]; then
    echo "usage: deploy_staging.sh <app-dir>" >&2
    exit 2
  fi
  cd "$1"

  echo "==> Pulling latest code"
  git pull --ff-only

  echo "==> Installing requirements"
  ./venv/bin/pip install -r requirements.txt

  echo "==> Running migrations"
  ./venv/bin/python manage.py migrate --noinput

  echo "==> Restarting Celery worker (byobu session: celery)"
  restart_session celery "./venv/bin/celery -A config worker -l info"

  echo "==> Restarting web server on port 9100 (byobu session: web)"
  # --noreload: otherwise git pull restarts the running server on new code
  # before migrations have run.
  restart_session web "./venv/bin/python manage.py runserver --noreload 0.0.0.0:9100"

  echo "==> Waiting for $HEALTH_URL"
  wait_for_health

  echo "==> Checking the Celery worker"
  check_session_alive celery
}

main "$@"
