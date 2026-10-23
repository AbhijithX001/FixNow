# Render demo initialization

The application does not initialize or seed data during startup. The
`initialize_demo_environment` management command is an explicit, one-time
operation for a clean disposable demo database.

## Render configuration

Build command:

```text
pip install -r requirements.txt && python manage.py collectstatic --noinput
```

Start command:

```text
gunicorn fixnow_pro.wsgi:application
```

Required environment variables:

```text
DJANGO_SECRET_KEY=<secret>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=<render-hostname>
DEFAULT_FROM_EMAIL=<sender>
BREVO_API_KEY=<Brevo API key>

AI_PROVIDER=gemini
GEMINI_API_KEY=<Gemini API key>
GEMINI_MODEL=<supported Gemini model>
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta

```

The AI chat endpoint no longer depends on Django's database-backed cache, so
the legacy `ai_rate_limit_cache` migration is not required for requests. The
historical migration remains unchanged for compatibility with databases where
it was already applied; do not remove or reverse that table as part of this
change.

## Initialization

Run these commands manually against the Render demo service or a one-off job,
after confirming the target database is the disposable demo database:

```text
python manage.py initialize_demo_environment
```

The command runs migrations and then invokes `seed_demo_data`. It refuses to
continue when protected application tables contain any records, and it never
deletes, resets, or overwrites an existing database. It is not imported by the
WSGI module, startup command, or any request path.

If the command refuses because the database already contains data, stop and
inspect the target before taking any action. Do not bypass the guard with a
reset or fixture load.

## Ephemeral SQLite recovery

Render's local SQLite filesystem is not durable across all redeployments or
instance replacement. User-created bookings and other changes may therefore be
lost. Recovery is to provision a fresh demo instance, run migrations, run
`initialize_demo_environment` once, and verify the customer login, marketplace
discovery, Gemini assistant, and booking handoff. Do not commit a database
snapshot or copy the local development `db.sqlite3` into the release.
