# Deploy Staging — Design

**Date:** 2026-10-07
**Status:** Draft for review

## Goal

Replace the fake `deploy` job in `.github/workflows/ci.yml` with a real job, **"deploy staging"**, that deploys `main` to the on-premise staging server once every other CI job has passed.

**Success means:** a push to `main` that passes `build`, `secrets` and `test` updates the staging server. Afterwards, the Celery worker and the Django server on port 9100 are running the new code, and the health check (`/health`) answers. If any step fails, the CI job fails.

## Requirements (from the request)

1. The deploy runs only when every other CI step is green.
2. The job is called "deploy staging".
3. It reaches the server over SSH with a user and password stored as GitHub secrets.
4. On the server it uses byobu, runs the Celery worker (`./venv/bin/celery -A config worker -l info`), `git pull`, `python manage.py migrate` and `python manage.py runserver 0.0.0.0:9100`.

## Assumptions

- The staging server is on-premise, and its SSH port can be reached from GitHub's cloud runners (`ubuntu-latest`). If it can't, see "Alternative: self-hosted runner".
- The server is already set up: the repo is cloned, `venv` exists, and byobu, Celery, its broker and SQLite are installed and configured. After `git pull`, `celery -A config` and `migrate` work on the server. This repo's app code is **not** changed by this work.
- Server environment variables (`SECRET_KEY`, `ALLOWED_HOSTS` including `localhost` for the health check and the server's address, the broker URL, …) are set in the deploy user's login profile, so a login shell (`bash -l`) picks them up.
- The server's working copy has no local changes, so `git pull --ff-only` succeeds.

## Design

### Components

| Unit | Purpose |
|------|---------|
| `scripts/deploy_staging.sh` | Runs **on the server**. Updates code and restarts the processes. Takes the app directory as its only argument. |
| `deploy-staging` job in `ci.yml` | Runs **on the runner**. Opens the SSH connection and pipes the script to the server. |
| `shellcheck` step in the `build` job | Lints the deploy script on every PR and push. |

### CI job

```yaml
deploy-staging:
  name: deploy staging
  needs: test            # test already needs build + secrets
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  runs-on: ubuntu-latest
  environment: staging
  concurrency:
    group: staging
    cancel-in-progress: false
```

Steps:
1. Check out the repo (to get the script).
2. Install `sshpass` (`sudo apt-get install -y sshpass`).
3. Write `STAGING_HOST_KEY` to `~/.ssh/known_hosts`. Host key checking stays **on**.
4. Run the script remotely:
   `sshpass -e ssh -p "$STAGING_PORT" "$STAGING_USER@$STAGING_HOST" "bash -ls -- '$STAGING_APP_DIR'" < scripts/deploy_staging.sh`
   with `SSHPASS` set from `STAGING_PASSWORD`, so the password never shows up in the command line or the logs.

The `staging` GitHub environment holds the secrets. A manual-approval rule can be added there later without changing any code.

### Secrets (environment `staging`)

| Secret | Example |
|--------|---------|
| `STAGING_HOST` | `staging.example.internal` or a public IP |
| `STAGING_PORT` | `22` |
| `STAGING_USER` | deploy user |
| `STAGING_PASSWORD` | deploy user's password |
| `STAGING_APP_DIR` | absolute path to the cloned repo on the server |
| `STAGING_HOST_KEY` | output of `ssh-keyscan -p <port> <host>`, captured once from a trusted network |

### Server script (`scripts/deploy_staging.sh`)

`set -euo pipefail`. Steps, in this order:

1. `cd "$1"` (the app directory).
2. `git pull --ff-only`
3. `./venv/bin/pip install -r requirements.txt`
4. `./venv/bin/python manage.py migrate --noinput`
5. Restart the Celery worker, then 6. the web server. For each byobu session (`celery`, `web`):
   - If the session exists, send `TERM` to its process and wait up to 30 seconds for it to exit. Closing a session alone sends `HUP`, which Celery treats as "restart" and which would leave an orphaned worker.
   - `byobu kill-session -t <name> 2>/dev/null || true`
   - `byobu new-session -d -s <name> -c "$PWD" bash -lc "exec <command>"`, then `byobu set-option -t <name> remain-on-exit on`, so a crashed process leaves its pane open to inspect.
   - Commands: `./venv/bin/celery -A config worker -l info` and `./venv/bin/python manage.py runserver --noreload 0.0.0.0:9100`. `--noreload` stops `git pull` from restarting the running server on new code before migrations run.
7. Health check: poll `curl -fsS --max-time 5 http://localhost:9100/health` every 2 seconds for up to 30 seconds. Exit non-zero if it never passes.
8. Check that the `celery` pane is still alive. Exit non-zero if it isn't.

**Why this order differs from the request:** the request starts Celery before `git pull`, which would leave the worker running the old code. The order here pulls, installs, migrates, then restarts both processes, so both run the new code.

**Why detached byobu sessions:** `runserver` and the Celery worker never exit. Detached sessions (`-d`) let the SSH command return so the CI job can finish, while the processes keep running after CI disconnects. You can still attach to them on the server with `byobu attach -t web` or `byobu attach -t celery`.

**Why stop before starting:** without it, a second deploy would leave the old worker running and would fail to bind port 9100.

### Error handling

- Every step that fails (pull, pip, migrate, health check) stops the script. The non-zero exit propagates through SSH and fails the CI job.
- If the health check fails, the byobu sessions are left as they are, so you can attach and see the error.
- There's no automatic rollback. To recover, push a fix or revert the commit; the next deploy picks it up.

### Testing

- `shellcheck scripts/deploy_staging.sh` in the `build` job (shellcheck is preinstalled on `ubuntu-latest`).
- The existing `pytest` suite already covers `/health`.
- Manual verification after merging: watch the first "deploy staging" run, then confirm `curl http://<host>:9100/health` from your network and `byobu ls` on the server (sessions `celery` and `web`).

### README

Add a "Deploy to staging" section listing the required secrets, how to capture `STAGING_HOST_KEY`, and how to attach to the byobu sessions.

## Alternative: self-hosted runner

If GitHub's cloud can't reach the server over SSH, install a GitHub self-hosted runner inside the network and change `runs-on` to its label. The rest of the design stays the same. If the runner is on the staging server itself, the job can run the script directly (`bash scripts/deploy_staging.sh "$STAGING_APP_DIR"`) and the SSH secrets aren't needed.

## Out of scope

- Replacing `runserver` with gunicorn or putting a reverse proxy in front.
- Switching from password to SSH-key authentication (recommended later).
- Automatic rollback, notifications, or deploying to production.
- Changes to the app's Celery or database configuration.
