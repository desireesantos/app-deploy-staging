"""Tests for scripts/deploy_staging.sh.

The script runs on the staging server. Here it runs against a fake app
directory: stub commands (git, byobu, curl, sleep, and venv/bin/pip, python,
celery) log each call, so we can check what runs and in what order without
a server. The script is fed on stdin, exactly as CI pipes it over SSH.
"""

import signal
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

# With $RUNNING_PID unset, behaves as on a first deploy: no sessions exist,
# so list-panes and kill-session fail. With it set, every session's pane runs
# that pid. The pane_dead query answers $PANE_DEAD (default 0, alive).
BYOBU_STUB = """#!/bin/sh
echo "byobu $*" >> "$CALL_LOG"
case "$1" in
  kill-session) exit 1 ;;
  list-panes)
    case "$*" in
      *pane_dead*) echo "${PANE_DEAD:-0}" ;;
      *) [ -n "$RUNNING_PID" ] || exit 1; echo "$RUNNING_PID" ;;
    esac ;;
esac
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
            check=False,
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
        "byobu list-panes -t celery -F #{pane_pid}",
        "byobu kill-session -t celery",
        (
            f"byobu new-session -d -s celery -c {app_dir} "
            "bash -lc exec ./venv/bin/celery -A config worker -l info"
        ),
        "byobu set-option -t celery remain-on-exit on",
        "byobu list-panes -t web -F #{pane_pid}",
        "byobu kill-session -t web",
        (
            f"byobu new-session -d -s web -c {app_dir} "
            "bash -lc exec ./venv/bin/python manage.py runserver --noreload 0.0.0.0:9100"
        ),
        "byobu set-option -t web remain-on-exit on",
        "curl -fsS --max-time 5 -o /dev/null http://localhost:9100/health",
        "byobu list-panes -t celery -F #{pane_dead}",
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


def test_stops_running_processes_with_term_before_restart(server):
    # Closing a session alone sends HUP, which Celery treats as "restart",
    # leaving an orphaned worker. The script must TERM the pane's process.
    running = subprocess.Popen(["/bin/sleep", "30"])
    try:
        result = run_deploy(server, RUNNING_PID=str(running.pid))

        assert result.returncode == 0, result.stderr
        assert running.wait(timeout=5) == -signal.SIGTERM
    finally:
        running.kill()


def test_fails_when_celery_worker_died(server):
    result = run_deploy(server, PANE_DEAD="1")

    assert result.returncode != 0
    assert "celery is not running" in result.stderr
    assert "byobu attach -t celery" in result.stderr
