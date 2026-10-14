# Lurg Notes server

This is a standalone entry point around the original Notes and shared Lurg Core
implementations. It exposes Notes, trusted-device E2EE v2, Lurg ID, Graph and
Android push-token/release APIs. `app.main` also runs the Notes/Graph outbox
publisher and the app-release push worker.

The database, authentication, Graph extraction, AI providers and realtime
outbox are shared dependencies of Notes. `app/modules/chat/` contains the
original candidate/extraction helpers used by Notes and Graph. Its HTTP routes
are not registered. Administration, operations, support, services catalog and
GitHub connector HTTP routes are not registered. No owner account is seeded.

## Local setup

Use Python 3.12 and a dedicated PostgreSQL database with the `vector` and
`pgcrypto` extensions. The schema uses `halfvec(3072)` and an HNSW index, which
requires pgvector with halfvec support (0.7 or newer).

Run the commands from `server/`:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Set `DATABASE_URL` and replace `JWT_SECRET` in `.env` with the generated value.
`DATABASE_URL` uses the `postgresql+asyncpg://` scheme. The example's blank
cookie domain and `COOKIE_SECURE=false` are for localhost. Set your deployment
cookie domain, HTTPS policy and browser origins when serving it publicly.

**The base schema must be initialized before Alembic.** Migration 0001 upgrades
existing `users`, `nodes` and `edges` tables; it does not create them. For an
empty dedicated database, initialize it once, migrate, then serve the API:

```sh
.venv/bin/python scripts/init_database.py
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The initializer refuses to modify a nonempty database schema. For a database
that already has the base schema, run `alembic upgrade head` separately after
checking its migration state. Do not rerun `schema.sql` against an existing
installation. The retained chain has all 27 revisions, ending at
`20261008_0027`.

The repository's Docker Compose mounts `schema.sql` into PostgreSQL's first-run
initialization directory. That runs only when its database volume is empty.
The API container then runs `alembic upgrade head` before Uvicorn. The image's
default command intentionally performs only migrations and API startup; base
initialization is a separate step.

## Optional providers

Registration, email verification and password recovery retain the original
Brevo integration. Configure `BREVO_API_KEY`, sender email and
`BREVO_TEMPLATE_VERIFY_EMAIL_ID` with your own account and template. There is no
development endpoint that exposes OTPs. Email registration cannot complete
until a mail provider is configured.

Notes uses `X-Lurg-App-Name: lurg_notes`. Migration 0020 registers that app with
the display name `Lurg Notes`. Android uses `/v1/auth` for authentication and
the same API process supplies those routes locally. The original JWT issuer
and ecosystem audience are preserved so the shared auth and LCP code agree.

Configure your own OpenRouter or Gemini credentials for editor AI actions.
Gemini embeddings are also used by Graph's semantic search. Normal encrypted
note synchronization does not need an AI credential. Web search is disabled in
the example; configure the original SearXNG integration if you enable it.

For realtime invalidations, set `LCP_EVENT_BUS_REDIS_URL` to the private Redis
used by LCP. Set the same `LCP_INTERNAL_TOKEN` in the API and LCP for live auth
session checks. Without Redis, the publisher disables itself and encrypted
HTTP synchronization still works. The outbox retains unpublished events until
the event bus is configured.

For release push notifications, configure your own Firebase Admin service
account file and `PUSH_INTERNAL_TOKEN`. Keep credentials outside this repository
and mount them separately. The original release metadata validates canonical
Lurg download URLs; changing a public distribution host requires a coordinated
client and release-service configuration change. Push delivery is disabled
when Firebase is not configured.

## Source scope and export changes

`app/modules/notes/`, the shared auth/identity/Graph/app/outbox implementations,
AI providers, runtime helpers, `schema.sql`, requirements and all Alembic files
are copied from `backend/` in the source workspace. Their application logic
and migration history are preserved. This includes shared schema tables for
chat, connectors and administration because the original migrations refer to
them; their presence does not expose those HTTP APIs.

The public export changes these supporting files:

- `app/main.py`: standalone Notes entry point, selected router registration,
  app-release push and outbox lifecycle, health/readiness and profile helpers.
- `app/config.py`: empty owner email default and `lurg_notes` app default.
- `.env.example`, `.dockerignore`, this README and `scripts/init_database.py`:
  public setup with placeholders and empty-schema initialization.
- `tests/conftest.py`: synthetic settings for unit tests.

The original Notes regression tests and dependency tests for identity,
security, AI/Graph extraction, safe fetch, outbox and release pushes are
included. PostgreSQL fixture helpers are included so Notes realtime tests have
their imports and full schema/migration prerequisites.

## Verification

Run the ordinary suite without a PostgreSQL integration target:

```sh
env -u LURG_TEST_DATABASE_URL .venv/bin/python -m pytest -q
```

This uses synthetic settings, mocked services and no production database.
Database integration tests skip unless `LURG_TEST_DATABASE_URL` is explicitly
provided. Their fixture accepts only a localhost database named `lurgtest` and
creates and drops disposable schemas. To run those tests, create a separate
test PostgreSQL instance with pgvector, install the development requirements
and explicitly point `LURG_TEST_DATABASE_URL` to it.

`/health` reports that the API process is responding; `/ready` additionally
checks PostgreSQL connectivity. Neither endpoint verifies mail delivery,
external AI, Firebase, Redis fanout or a physical Android device.
