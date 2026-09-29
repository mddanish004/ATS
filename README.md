# HireFlow — Production-Grade Multi-Tenant ATS

HireFlow is an applicant tracking system for small recruiting teams: organizations,
role-based recruiting workflows, job requisitions with approval, a public careers
site, candidate applications with resume upload, and asynchronous resume processing.

**Current milestone: backend/API-first (Week 1).** There is no frontend yet. The
working slice is a FastAPI modular monolith with authenticated APIs, public
careers/application endpoints, tenant isolation, and a Redis/ARQ resume pipeline
whose processor is currently a placeholder (real parsing/AI is a later milestone).

## Architecture

- **FastAPI modular monolith** (`apps/api/app`): `api/v1` routes, `services`
  domain logic, `models`/`schemas`, `workers`, `core`, `db`
- **SQLModel** models, **Alembic** migrations
- **Neon PostgreSQL** as the system of record (no local Postgres container)
- **Redis** as queue backend, **ARQ worker** for resume jobs
- **Private storage abstraction**: local disk in development, Cloudflare R2 in
  production; tenant-scoped keys, signed-URL support in the abstraction
- **Docker** image + Compose (API, worker, Redis)
- Quality gates: **pytest** (307 tests), **Ruff**, **MyPy**

See [docs/architecture/architecture.md](docs/architecture/architecture.md) for the
component diagram and request flows.

## Repository structure

```text
.
├── apps/api/                  # FastAPI backend (the implemented slice)
│   ├── app/
│   │   ├── main.py            # app factory, GET /health, GET /ready
│   │   ├── api/v1/            # auth, organizations, jobs, public_careers
│   │   ├── core/              # config, errors/envelope, readiness, security
│   │   ├── db/                # engine/session, tenant helpers, dev seed
│   │   ├── models/            # SQLModel tables
│   │   ├── schemas/           # Pydantic request/response validation
│   │   ├── services/          # resume pipeline, storage, queue, validation
│   │   └── workers/           # ARQ resume worker
│   ├── migrations/versions/   # Alembic revisions
│   ├── tests/                 # pytest suite
│   ├── Dockerfile             # production API image
│   └── README.md              # API-level run instructions
├── docker-compose.yml         # api + worker + redis (no local Postgres)
├── .env.example               # all runtime settings (no secrets)
├── HireFlow_PRD.md            # full product plan (includes future scope)
└── docs/architecture/         # Week 1 architecture documentation
```

## Prerequisites

- Python 3.14+ with [uv](https://docs.astral.sh/uv/)
- A **Neon PostgreSQL** connection string (local Postgres is not used)
- Docker engine (only for container/Compose runs; not needed for local dev)
- Redis for local dev: `docker compose up redis` (or any reachable Redis)

## Environment setup

```bash
cp .env.example apps/api/.env   # then fill in real values; never commit them
```

Required settings (see [.env.example](.env.example) for the full list):

| Variable | Purpose |
| --- | --- |
| `APP_ENV` / `ENVIRONMENT` | `development` locally; seed + strict guards key off this |
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `JWT_SECRET_KEY` | signing key (≥ 32 chars enforced in production) |
| `REDIS_URL` | Redis connection string |
| `R2_ENDPOINT_URL`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Cloudflare R2 (only when `RESUME_STORAGE_BACKEND=r2`) |
| `CORS_ALLOWED_ORIGINS` | comma-separated or JSON array; empty = same-origin only, `*` rejected in production |
| `RESUME_*`, token expiries, `APP_NAME`, `JWT_ALGORITHM` | queue tuning, lifetimes, naming |

## Database setup

```bash
cd apps/api
uv run alembic upgrade head   # apply migrations (also runs on API container boot)
uv run alembic check          # verify no model/migration drift
```

## Run the API

```bash
cd apps/api
uv run uvicorn app.main:app --reload
```

## Run the worker

```bash
cd apps/api
uv run arq app.workers.resume_worker.WorkerSettings
```

## Redis

Local dev uses the Compose Redis service (`docker compose up redis`) or any
`REDIS_URL`-reachable instance. The API and worker must share the same
`REDIS_URL`. Redis holds the resume queue only — never source-of-truth records.

## Seed data

Deterministic local dataset (org `acme`, 4 role users, 4 jobs, 2 applications).
Database rows only — no binaries, R2, Redis, or AI. **Refuses production/staging;
safe to re-run.**

```bash
cd apps/api
uv run python -m app.db.seed
```

Local credentials: `admin@` / `recruiter@` / `hiring-manager@` /
`interviewer@example.com`, password `DevPassword123` (see
[apps/api/README.md](apps/api/README.md)).

## Tests and checks

```bash
cd apps/api
uv run pytest                                       # full suite (307 tests)
uv run pytest tests/test_deployment.py -q           # deployment slice
uv run pytest tests/test_seed.py -q                 # seed data
uv run ruff check <touched-files>                   # lint
uv run ruff format --check <touched-files>          # formatting
uv run mypy app/                                    # types (clean, 43 files)
uv run alembic check                                # migration drift
git diff --check                                    # whitespace (from repo root)
```

## Docker

- `apps/api/Dockerfile`: baked `uv sync --frozen` deps, non-root user, migrate-then-serve
  single-release startup, `/health` healthcheck, no secrets in the image.
- Compose services: `api` (8000), `worker` (ARQ), `redis`. No Postgres service —
  the database is Neon via `DATABASE_URL`.

```bash
cp .env.example apps/api/.env   # fill in real values first
docker compose up --build
docker compose config            # static validation (needs only the CLI)
```

Live container startup requires a running Docker daemon.

## Health and readiness

- `GET /health` → `{"status": "ok"}`. Liveness: the process is alive, no checks.
- `GET /ready` → `{"status":"ready","checks":{"database":"up","redis":"up"}}` when
  PostgreSQL and Redis are reachable, else `503` error envelope. Readiness:
  safe to send traffic.

## Error handling

All API failures use one envelope:

```json
{ "error": { "code": "NOT_FOUND", "message": "Organization not found", "request_id": "…" } }
```

Every response (success and error) carries an `X-Request-ID` header matching the
envelope's `request_id`. Send your own `X-Request-ID` to propagate it; otherwise
one is generated. Error bodies never contain stack traces, SQL, connection
strings, credentials, or tokens.

## Security (implemented)

- Argon2 password hashing; generic 401s that don't reveal account existence
- Short-lived JWT access tokens + rotating HttpOnly refresh cookies; single-use
  expiring email-verification and password-reset tokens (hash-only storage)
- RBAC (admin/recruiter/hiring-manager/interviewer) enforced server-side
- Tenant isolation: server-resolved org context; cross-tenant access returns 404
  without disclosing other tenants
- Private resume storage with tenant-scoped keys; signed-URL generation in the
  storage abstraction (no public download endpoint yet)
- Production boot guards: JWT secret length, PostgreSQL-only DB URL, managed
  Redis, no wildcard CORS; `.env` files gitignored, no secrets in images

Not implemented: rate limiting, security headers, Sentry, email delivery.

## Deployment

Current slice: containerized API + ARQ worker + Redis (Compose), Neon Postgres,
R2-capable storage, env-only config, startup migrations, `/health` + `/ready`.
**No live provider deployment has occurred** — that is the next Day 7 step.

## Known limitations (intentionally later)

No frontend or frontend tests; placeholder resume processor (no PDF parsing, AI,
LangGraph, or Groq); no search, interviews, scorecards, offers, Resend email,
notifications, analytics, CI/CD, Sentry, rate limiting, or security headers.
