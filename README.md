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

Publishing a version does not deploy anything. The running app (staging) is only updated by changes merged to `main`.

| Trigger | What happens |
|---|---|
| Push / merge to `main` | CI runs build and test, then deploys to **staging** |
| Tag `vX.Y.Z-beta.N` / `vX.Y.Z-rc.N` | Tests run and a public GitHub **pre-release** is created |
| Tag `vX.Y.Z` | Tests run and a public GitHub **release** is created |

Tags must point to a commit already on `main`.

```bash
git checkout main && git pull
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```
