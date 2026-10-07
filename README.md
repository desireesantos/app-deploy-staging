# app-deploy-staging

[![CI](https://github.com/desireesantos/app-deploy-staging/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/desireesantos/app-deploy-staging/actions/workflows/ci.yml)

Repositório para testes e validação da automação de deploy do aplicativo em ambiente stagging(teste)

## Hello World (Django)

Minimal Django app with no database (Python 3.12, Django 5, pytest-django, gunicorn).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

DEBUG=True python manage.py runserver   # dev: http://127.0.0.1:8000
pytest                                  # tests
ruff check . && ruff format --check .  # lint
gunicorn config.wsgi:application        # production server
```

Enable the pre-commit hook (scans for secrets and runs tests before each commit; once per clone):

```bash
brew install talisman                   # secret scanner used by the hook
git config core.hooksPath .githooks
```

If Talisman flags a false positive, it prints the `.talismanrc` entry to add. CI also scans every push and pull request with [Gitleaks](https://github.com/gitleaks/gitleaks), and a finding blocks the tests and the deploy.

`DEBUG` defaults to `False`; set `DEBUG=True` only for local development. In any deployed environment, always set `SECRET_KEY` and `ALLOWED_HOSTS`.

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
