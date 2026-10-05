# app-deploy-staging
Repositório para testes e validação da automação de deploy do aplicativo em ambiente stagging(teste)

## Hello World (Django)

Minimal Django app with no database (Python 3.12, Django 5, pytest-django, gunicorn).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py runserver              # dev: http://127.0.0.1:8000
pytest                                  # tests
gunicorn config.wsgi:application        # production server
```

Enable the pre-commit hook (runs tests before each commit; once per clone):

```bash
git config core.hooksPath .githooks
```
