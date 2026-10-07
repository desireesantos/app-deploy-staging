# Deploy Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fake `deploy` CI job with a real "deploy staging" job that deploys `main` to the on-premise staging server over SSH once every other job has passed.

**Architecture:** The server-side steps live in a version-controlled Bash script, `scripts/deploy_staging.sh`. It pulls, installs, migrates, restarts Celery and `runserver` in detached byobu sessions, then polls `/health`. A new `deploy-staging` job in `.github/workflows/ci.yml` pipes that script to the server with `sshpass` + `ssh`, using credentials from the `staging` GitHub environment. The script is tested locally with pytest by running it against a fake app directory full of stub commands.

**Tech Stack:** Bash, GitHub Actions, sshpass/OpenSSH, byobu (tmux backend), pytest, shellcheck.

**Spec:** `docs/superpowers/specs/2026-10-07-deploy-staging-design.md`

## Global Constraints

- The job's display name is exactly `deploy staging`, and its job id is `deploy-staging`.
- The job runs only after every other CI job passes (`needs: test`, and `test` already needs `build` and `secrets`), and only on `push` to `refs/heads/main`.
- It uses GitHub environment `staging` and `concurrency` group `staging` with `cancel-in-progress: false`.
- Secrets: `STAGING_HOST`, `STAGING_PORT`, `STAGING_USER`, `STAGING_PASSWORD`, `STAGING_APP_DIR`, `STAGING_HOST_KEY`.
- The password reaches ssh only through the `SSHPASS` environment variable (`sshpass -e`), never on a command line.
- Host key checking stays on: `STAGING_HOST_KEY` is written to `~/.ssh/known_hosts`.
- Server order: `git pull --ff-only` → `./venv/bin/pip install -r requirements.txt` → `./venv/bin/python manage.py migrate --noinput` → restart byobu session `celery` running `./venv/bin/celery -A config worker -l info` → restart byobu session `web` running `./venv/bin/python manage.py runserver --noreload 0.0.0.0:9100` → health check → Celery pane alive. (Review fixes; see the spec's server script section.)
- Health check: `http://localhost:9100/health`, every 2 seconds, up to 30 seconds (15 attempts).
- The app code (`config/`, `hello/`) is **not** changed. Celery and SQLite are assumed to work on the server after `git pull`.

## Review Focus

1. **A command that reads stdin swallows the rest of the piped script.** The script arrives on stdin (`bash -s`), so if `pip` or `python` reads stdin, the later steps silently never run. Expected: every step still runs. Pinned in Task 1 by a `pip` stub that drains stdin, combined with the happy-path test.
2. **First deploy, when no byobu sessions exist yet.** `byobu kill-session` exits non-zero. Expected: the deploy continues and starts both sessions. Pinned in Task 1 because the `byobu` stub fails every `kill-session`.
3. **`git pull` or `migrate` fails.** Expected: the job fails and the running Celery and web processes are left untouched, so staging keeps serving the old version. Pinned in Task 1 by two failure tests.
4. **The server takes a few seconds to start.** Expected: the health check retries and passes. It fails, with a pointer to `byobu attach -t web`, only after 15 attempts. Pinned in Task 1 by the slow-start and never-healthy tests.
5. **`STAGING_APP_DIR` contains spaces.** Expected: works, because every path is quoted. Pinned in Task 1 because the fake app directory is named `staging app`.

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/deploy_staging.sh` | Create | Runs on the server: update code, restart processes, health check. |
| `tests/__init__.py` | Create | Makes `tests` a package, like `hello/tests`. |
| `tests/test_deploy_staging.py` | Create | Runs the script against stub commands and checks calls, order and exit codes. |
| `.github/workflows/ci.yml` | Modify | Add `shellcheck` to `build`; replace the `deploy` job with `deploy-staging`. |
| `README.md` | Modify | "Deploy to staging" section: secrets, host key, byobu sessions. |
| `.talismanrc` | Create only if Talisman flags a false positive | Talisman's ignore entries. |

---

### Task 1: Deploy script with tests

**Files:**
- Create: `scripts/deploy_staging.sh`
- Create: `tests/__init__.py`
- Test: `tests/test_deploy_staging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/deploy_staging.sh <app-dir>`. It runs via `bash -s -- <app-dir> < scripts/deploy_staging.sh` (or `bash scripts/deploy_staging.sh <app-dir>`). Exit codes: 0 on success, 2 on wrong usage, non-zero when any step or the health check fails. Task 2's CI job calls it exactly this way.

- [ ] **Step 1: Write the failing tests**

Create an empty `tests/__init__.py`.

Create `tests/test_deploy_staging.py`:

```python
"""Tests for scripts/deploy_staging.sh.

The script runs on the staging server. Here it runs against a fake app
directory: stub commands (git, byobu, curl, sleep, and venv/bin/pip, python,
celery) log each call, so we can check what runs and in what order without
a server. The script is fed on stdin, exactly as CI pipes it over SSH.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy_staging.sh"

# Logs "<name> <args>" and fails when its name is listed in $FAIL_COMMANDS.
GENERIC_STUB = """#!/bin/sh
name=$(basename "$0")
echo "$name $*" >> "$CALL_LOG"
case " $FAIL_COMMANDS " in *" $name "*) exit 1 ;; esac
"""

# Like GENERIC_STUB, but drains stdin first. If the script didn't guard
# against it, this would swallow the rest of the piped script.
STDIN_READING_STUB = """#!/bin/sh
cat > /dev/null
name=$(basename "$0")
echo "$name $*" >> "$CALL_LOG"
case " $FAIL_COMMANDS " in *" $name "*) exit 1 ;; esac
"""

# kill-session always fails, as on a first deploy with no sessions yet.
BYOBU_STUB = """#!/bin/sh
echo "byobu $*" >> "$CALL_LOG"
[ "$1" = "kill-session" ] && exit 1
exit 0
"""

# Succeeds from attempt number $CURL_OK_ON_ATTEMPT onwards.
CURL_STUB = """#!/bin/sh
echo "curl $*" >> "$CALL_LOG"
attempts=$(grep -c '^curl ' "$CALL_LOG")
[ "$attempts" -ge "$CURL_OK_ON_ATTEMPT" ]
"""


def _write_stub(path, body):
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def server(tmp_path):
    # Space in the name on purpose: every path in the script must be quoted.
    app_dir = tmp_path / "staging app"
    venv_bin = app_dir / "venv" / "bin"
    bin_dir = tmp_path / "bin"
    venv_bin.mkdir(parents=True)
    bin_dir.mkdir()

    _write_stub(bin_dir / "git", GENERIC_STUB)
    _write_stub(bin_dir / "sleep", GENERIC_STUB)
    _write_stub(bin_dir / "byobu", BYOBU_STUB)
    _write_stub(bin_dir / "curl", CURL_STUB)
    _write_stub(venv_bin / "pip", STDIN_READING_STUB)
    _write_stub(venv_bin / "python", GENERIC_STUB)
    _write_stub(venv_bin / "celery", GENERIC_STUB)

    return {"app_dir": app_dir, "bin_dir": bin_dir, "log": tmp_path / "calls.log"}


def run_deploy(server, *args, **env):
    """Pipe the script to bash on stdin, the same way CI does over SSH."""
    if not args:
        args = (str(server["app_dir"]),)
    full_env = {
        "PATH": f"{server['bin_dir']}:/usr/bin:/bin",
        "CALL_LOG": str(server["log"]),
        "FAIL_COMMANDS": "",
        "CURL_OK_ON_ATTEMPT": "1",
        **env,
    }
    with SCRIPT.open() as script:
        return subprocess.run(
            ["bash", "-s", "--", *args],
            stdin=script,
            capture_output=True,
            text=True,
            env=full_env,
            timeout=30,
        )


def calls(server):
    if not server["log"].exists():
        return []
    return server["log"].read_text().splitlines()


def test_deploys_in_order(server):
    result = run_deploy(server)

    assert result.returncode == 0, result.stderr
    app_dir = server["app_dir"]
    assert calls(server) == [
        "git pull --ff-only",
        "pip install -r requirements.txt",
        "python manage.py migrate --noinput",
        "byobu kill-session -t celery",
        f"byobu new-session -d -s celery -c {app_dir} "
        "bash -lc './venv/bin/celery -A config worker -l info'",
        "byobu kill-session -t web",
        f"byobu new-session -d -s web -c {app_dir} "
        "bash -lc './venv/bin/python manage.py runserver 0.0.0.0:9100'",
        "curl -fsS -o /dev/null http://localhost:9100/health",
    ]


def test_leaves_processes_alone_when_pull_fails(server):
    result = run_deploy(server, FAIL_COMMANDS="git")

    assert result.returncode != 0
    assert calls(server) == ["git pull --ff-only"]


def test_leaves_processes_alone_when_migrate_fails(server):
    result = run_deploy(server, FAIL_COMMANDS="python")

    assert result.returncode != 0
    assert calls(server)[-1] == "python manage.py migrate --noinput"
    assert not any(call.startswith("byobu") for call in calls(server))


def test_waits_for_slow_server(server):
    result = run_deploy(server, CURL_OK_ON_ATTEMPT="3")

    assert result.returncode == 0, result.stderr
    assert sum(call.startswith("curl ") for call in calls(server)) == 3
    assert calls(server).count("sleep 2") == 2


def test_fails_when_health_check_never_passes(server):
    result = run_deploy(server, CURL_OK_ON_ATTEMPT="999")

    assert result.returncode != 0
    assert sum(call.startswith("curl ") for call in calls(server)) == 15
    assert "Health check failed" in result.stderr
    assert "byobu attach -t web" in result.stderr


def test_requires_app_dir_argument(server):
    result = run_deploy(server, "", "extra")

    assert result.returncode == 2
    assert "usage: deploy_staging.sh <app-dir>" in result.stderr
    assert calls(server) == []
```

Note on `test_requires_app_dir_argument`: it passes two arguments because `run_deploy` with no arguments supplies the app directory. Two arguments is a usage error just like zero.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_deploy_staging.py -v`
Expected: all 6 FAIL. `SCRIPT.open()` raises `FileNotFoundError` because `scripts/deploy_staging.sh` doesn't exist yet.

- [ ] **Step 3: Write the script**

Create `scripts/deploy_staging.sh`:

```bash
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

# Replaces a detached byobu session so the process outlives the SSH session.
# On a first deploy the session doesn't exist yet, so kill-session may fail.
restart_session() {
  local name="$1" command="$2"
  byobu kill-session -t "$name" 2>/dev/null || true
  byobu new-session -d -s "$name" -c "$PWD" "bash -lc '$command'"
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 "$HEALTH_ATTEMPTS"); do
    if curl -fsS -o /dev/null "$HEALTH_URL"; then
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
  restart_session web "./venv/bin/python manage.py runserver 0.0.0.0:9100"

  echo "==> Waiting for $HEALTH_URL"
  wait_for_health
}

main "$@"
```

Then make it executable: `chmod +x scripts/deploy_staging.sh`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_deploy_staging.py -v`
Expected: 6 passed.

Then run the whole suite and the linters: `venv/bin/pytest -q && venv/bin/ruff check . && venv/bin/ruff format --check .`
Expected: 8 passed (2 existing + 6 new), and no ruff findings. If `ruff format --check` complains, run `venv/bin/ruff format tests/` and re-run.

- [ ] **Step 5: Shellcheck the script**

Run: `shellcheck scripts/deploy_staging.sh` (install with `brew install shellcheck` if missing)
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy_staging.sh tests/__init__.py tests/test_deploy_staging.py
git commit -m "Add staging deploy script with tests"
```

The pre-commit hook runs Talisman and pytest. If Talisman flags a false positive, add the `fileignoreconfig` entry it prints to `.talismanrc`, `git add .talismanrc`, and commit again.

---

### Task 2: "deploy staging" CI job

**Files:**
- Modify: `.github/workflows/ci.yml` (the `build` job's steps, and the `deploy` job at the end of the file)

**Interfaces:**
- Consumes: `scripts/deploy_staging.sh <app-dir>` from Task 1, fed on stdin to `bash -ls -- <app-dir>`.
- Produces: job id `deploy-staging`, named `deploy staging`, which reads the six `STAGING_*` secrets from environment `staging` (configured in Task 4).

- [ ] **Step 1: Add shellcheck to the `build` job**

In `.github/workflows/ci.yml`, in the `build` job, add this step right after the `Check formatting` step:

```yaml
      - name: Lint shell scripts
        run: shellcheck scripts/*.sh
```

(shellcheck is preinstalled on `ubuntu-latest`.)

- [ ] **Step 2: Replace the fake `deploy` job**

Delete the whole `deploy:` job (from `  deploy:` to the end of the file) and put this in its place:

```yaml
  deploy-staging:
    name: deploy staging
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: staging
    concurrency:
      group: staging
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4
      - name: Install sshpass
        run: sudo apt-get update && sudo apt-get install -y sshpass
      - name: Trust staging host key
        env:
          STAGING_HOST_KEY: ${{ secrets.STAGING_HOST_KEY }}
        run: |
          mkdir -p ~/.ssh
          chmod 700 ~/.ssh
          echo "$STAGING_HOST_KEY" >> ~/.ssh/known_hosts
          chmod 600 ~/.ssh/known_hosts
      - name: Deploy and health check
        env:
          SSHPASS: ${{ secrets.STAGING_PASSWORD }}
          STAGING_HOST: ${{ secrets.STAGING_HOST }}
          STAGING_PORT: ${{ secrets.STAGING_PORT }}
          STAGING_USER: ${{ secrets.STAGING_USER }}
          STAGING_APP_DIR: ${{ secrets.STAGING_APP_DIR }}
        run: |
          sshpass -e ssh -p "$STAGING_PORT" \
            -o StrictHostKeyChecking=yes \
            -o PreferredAuthentications=password \
            -o PubkeyAuthentication=no \
            "$STAGING_USER@$STAGING_HOST" \
            "bash -ls -- '$STAGING_APP_DIR'" < scripts/deploy_staging.sh
```

Why each piece is there:
- `needs: test` is enough to require all green, because `test` already needs `build` and `secrets`.
- `bash -l` loads the deploy user's login profile, which is where `SECRET_KEY`, `ALLOWED_HOSTS` and the broker URL live.
- `-s -- '<dir>'` makes bash read the script from stdin and pass the app directory as `$1`.
- `StrictHostKeyChecking=yes` refuses to connect if the host key doesn't match `STAGING_HOST_KEY`.

- [ ] **Step 3: Validate the workflow**

Run: `actionlint .github/workflows/ci.yml` (install with `brew install actionlint`)
Expected: no output, exit 0.

Also check the job graph by eye: `grep -n "needs:\|^  [a-z-]*:$" .github/workflows/ci.yml`
Expected: `build`, `secrets`, `test` (needs `[build, secrets]`), `deploy-staging` (needs `test`), and no `deploy:` job left.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Replace fake deploy with real deploy staging job"
```

---

### Task 3: README section

**Files:**
- Modify: `README.md` (append after the last paragraph, the one starting "`DEBUG` defaults to `False`")

**Interfaces:**
- Consumes: the secret names from Task 2 and the session names `celery`/`web` from Task 1.
- Produces: documentation only.

- [ ] **Step 1: Append the section**

Add to the end of `README.md`:

````markdown
## Deploy to staging

Every push to `main` that passes `build`, `secrets` and `test` runs the **deploy staging** job. It connects to the on-premise staging server over SSH and runs [`scripts/deploy_staging.sh`](scripts/deploy_staging.sh) there: `git pull` → `pip install` → `migrate` → restart the Celery worker and `runserver 0.0.0.0:9100` in byobu → wait for `/health`.

The job reads these secrets from the `staging` GitHub environment (Settings → Environments → staging):

| Secret | Value |
|--------|-------|
| `STAGING_HOST` | Server hostname or IP |
| `STAGING_PORT` | SSH port, usually `22` |
| `STAGING_USER` | Deploy user |
| `STAGING_PASSWORD` | Deploy user's password |
| `STAGING_APP_DIR` | Absolute path of the cloned repo on the server |
| `STAGING_HOST_KEY` | Output of `ssh-keyscan -p <port> <host>`, run once from a trusted network |

The server must already have the repo cloned with a `venv`, plus byobu, Celery with its broker, and SQLite. Environment variables such as `SECRET_KEY` and `ALLOWED_HOSTS` (which must include `localhost` and the server's address) go in the deploy user's login profile.

On the server, the processes run in detached byobu sessions:

```bash
byobu ls                 # sessions: celery, web
byobu attach -t web      # Django server logs (detach with F6)
byobu attach -t celery   # Celery worker logs
```

To deploy by hand from the server: `bash scripts/deploy_staging.sh "$PWD"`.
````

- [ ] **Step 2: Check rendering**

Run: `grep -n "## Deploy to staging" README.md`
Expected: one match, after the "Hello World (Django)" section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the staging deploy"
```

If Talisman flags the README (it reacts to words like `PASSWORD`), add the `fileignoreconfig` entry it prints to `.talismanrc`, `git add .talismanrc`, and commit again.

---

### Task 4: GitHub setup and first deploy (manual, needs the repo owner)

This task needs credentials and access to the staging server, so the repo owner does it, not an agent. Do Steps 1–3 **before merging** the PR. Otherwise the first push to `main` runs the job with no secrets and fails.

- [ ] **Step 1: Dry-run the script exactly as CI runs it**

First check the byobu backend on the server: `ssh <user>@<host> 'cat ~/.byobu/backend 2>/dev/null'`. Expect `BYOBU_BACKEND=tmux`, or no file (tmux is the default). If it says `screen`, run `byobu-select-backend tmux` on the server; the script uses tmux flags.

Then, from your laptop, on the branch with Task 1, run the same command as the CI job. There's no tty, the script arrives on stdin, and bash runs as a login shell. That flushes out profile side effects (such as a `byobu-launch` line in `~/.profile`) and git credential prompts before the first real deploy:

```bash
read -rs SSHPASS && export SSHPASS     # type the deploy password; it isn't echoed
sshpass -e ssh -p <port> -o PreferredAuthentications=password,keyboard-interactive \
  <user>@<host> "bash -ls -- '<absolute app dir>'" < scripts/deploy_staging.sh
unset SSHPASS
```

Expected: the steps print in order, ending with `Health check passed on attempt N.` and exit 0. On the server, `byobu ls` shows sessions `celery` and `web`. Run it a second time, then check `pgrep -af 'celery -A config worker'` on the server: there should be exactly one worker (no orphans from the first run).

- [ ] **Step 2: Capture the host key from a trusted network**

```bash
ssh-keyscan -p <port> <host> > staging_host_key.txt
cat staging_host_key.txt   # compare the fingerprint with: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub (on the server)
```

- [ ] **Step 3: Create the environment and secrets**

```bash
gh api -X PUT repos/desireesantos/app-deploy-staging/environments/staging
gh secret set STAGING_HOST     --env staging --body "<host>"
gh secret set STAGING_PORT     --env staging --body "<port>"
gh secret set STAGING_USER     --env staging --body "<user>"
gh secret set STAGING_APP_DIR  --env staging --body "<absolute app dir>"
gh secret set STAGING_HOST_KEY --env staging < staging_host_key.txt
gh secret set STAGING_PASSWORD --env staging        # prompts; keeps the password out of shell history
rm staging_host_key.txt
gh secret list --env staging
```

Expected: `gh secret list` shows all six names.

- [ ] **Step 4: Open the PR, merge, and watch the first deploy**

```bash
git push -u origin <branch>
gh pr create --fill
```

On the PR, `build` (including "Lint shell scripts"), `secrets` and `test` go green, and **deploy staging doesn't run**: it only runs on push to `main`. After merging:

```bash
gh run watch          # pick the run for the merge commit
```

Expected: `deploy staging` succeeds with `Health check passed`. From your network, `curl http://<host>:9100/health` returns `{"status": "ok"}`.

If the SSH step can't connect (timeout), GitHub's cloud can't reach the server. Follow the spec's "Alternative: self-hosted runner" section.
