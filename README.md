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
gunicorn config.wsgi:application        # production server
```

Enable the pre-commit hook (runs tests before each commit; once per clone):

```bash
git config core.hooksPath .githooks
```

`DEBUG` defaults to `False`; set `DEBUG=True` only for local development. In any deployed environment, always set `SECRET_KEY` and `ALLOWED_HOSTS`.

## Releases

The public final version lives on the `release` branch. Releasing never deploys anything: the running app (staging) is only updated by changes merged to `main`.

| Branch | What happens |
|---|---|
| `main` | CI runs build and test, then deploys to **staging** |
| `release` | CI runs build and test. No deploy |

To publish a new final version, open a pull request from `main` into `release` and merge it once CI passes:

```bash
gh pr create --base release --head main --title "Release"
```
