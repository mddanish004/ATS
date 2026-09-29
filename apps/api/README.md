# HireFlow API

## Local development

Start Redis:

```bash
cd /home/mddanish/ats
docker compose up redis
```

Start the API:

```bash
cd /home/mddanish/ats/apps/api
uv run uvicorn app.main:app --reload
```

Start the resume-processing worker:

```bash
cd /home/mddanish/ats/apps/api
uv run arq app.workers.resume_worker.WorkerSettings
```

For local Docker worker execution:

```bash
cd /home/mddanish/ats
docker compose up worker
```

## Seed development data

Creates a deterministic local dataset (organization `acme`, four users,
four jobs, two applications with resume references). Database records only:
no resume binaries, no R2, no Redis/ARQ, no AI processing. Requires applied
migrations. Refuses to run in production/staging and is safe to re-run.

```bash
cd /home/mddanish/ats/apps/api
uv run python -m app.db.seed
```

Development credentials (local use only):

| Role | Email | Password |
| --- | --- | --- |
| Admin | admin@example.com | DevPassword123 |
| Recruiter | recruiter@example.com | DevPassword123 |
| Hiring manager | hiring-manager@example.com | DevPassword123 |
| Interviewer | interviewer@example.com | DevPassword123 |

## Production deployment slice

The API ships as a container image with migrations applied at startup
(single-release strategy; use a separate release step for multi-replica
deploys). Configuration is environment-only (see `/home/mddanish/ats/.env.example`).
The project uses Neon PostgreSQL, so compose provides Redis + API + worker
but no local database service.

```bash
cd /home/mddanish/ats
cp .env.example apps/api/.env  # then fill in real values
docker compose up --build
```

- Liveness: `GET /health` (process alive, no dependency checks).
- Readiness: `GET /ready` (PostgreSQL + Redis reachable, else 503).
- CORS is opt-in via `CORS_ALLOWED_ORIGINS` (comma-separated or JSON
  array); empty means same-origin only, and wildcards are rejected in
  production.
