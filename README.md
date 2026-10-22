# ABC Operations Portal (public review copy)

A Django web app for running an oilfield-services operation: equipment and certificates, maintenance, an action tracker, QHSE (follow-up, investigations, audits, site visits, incidents), job manager and job reports, SQB and NPT tracking, SGS pressure-survey reports, personnel and training certificates, approvals, notifications, and a private iPad whiteboard.

**This copy is shared so people can read it and send comments.** It contains no real data, no customer names and no secrets. Names such as "Client A" and "Site A" are placeholders.

## What to comment on

- Is anything confusing, missing or in the wrong place?
- Are the permission levels (Viewer, Editor, Level 3, Admin) and the department separation sensible?
- Security or data-handling concerns.

Open an **Issue** (see the templates) or start a **Discussion**. Please do not post real company data, names or credentials.

## Stack

Django 5.1, Python 3.13, PostgreSQL (SQLite for local use), WhiteNoise, gunicorn, S3-compatible storage (optional).

## Run it locally

```bash
cd neos_portal
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set DJANGO_SECRET_KEY to any long random string
python manage.py migrate
python manage.py seed_sample_data
python manage.py createsuperuser
python manage.py runserver
```

## Run the tests

```bash
DEBUG=True DJANGO_SECRET_KEY=test DATABASE_URL=sqlite:///test.sqlite3 \
DJANGO_SETTINGS_MODULE=neos_portal.settings_test python manage.py test
```

## Status

Internal tool, in active use. The code is shared for review only; see `LICENSE`.
