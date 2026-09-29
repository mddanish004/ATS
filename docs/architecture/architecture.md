# HireFlow Week 1 Architecture

Backend/API-first milestone. Documents **only what is implemented**; product
direction beyond this lives in [HireFlow_PRD.md](../../HireFlow_PRD.md).

## Component diagram

```mermaid
flowchart TB
    Client["Client / API consumer<br/>(no frontend yet)"] --> API

    subgraph API["FastAPI modular monolith (apps/api/app)"]
        Auth["Auth<br/>register, login, refresh,<br/>verify, password reset"]
        Orgs["Organizations<br/>+ memberships"]
        RBAC["RBAC / tenant context<br/>roles, permissions,<br/>org-scoped queries"]
        Jobs["Jobs<br/>CRUD + approval lifecycle"]
        Careers["Public careers<br/>job listing + detail"]
        Applications["Applications<br/>public apply + upload"]
        ResumeSvc["Resume services<br/>validation, malware hook,<br/>storage, queue, processing"]
    end

    API --> Postgres[("Neon PostgreSQL<br/>(SQLModel + Alembic)<br/>system of record")]
    API --> Redis[("Redis<br/>resume queue only")]

    Redis --> Worker["ARQ worker<br/>(app.workers.resume_worker)"]
    Worker --> Processing["Resume processing<br/>(placeholder:<br/>QUEUED → NEEDS_REVIEW)"]
    Processing --> Postgres

    API --> Storage{"Private storage abstraction"}
    Storage --> LocalDisk["Local disk<br/>(development)"]
    Storage --> R2["Cloudflare R2<br/>(production)"]

    Seed["Dev seed<br/>(python -m app.db.seed)"] -.-> Postgres
```

Supporting pieces (same repo, off the request path): pytest/Ruff/MyPy gates,
`GET /health` (liveness) and `GET /ready` (Postgres + Redis reachability),
PRD error envelope with request IDs, Docker image + Compose (`api`, `worker`,
`redis`; no local Postgres container).

## Request flows

**Public application** (unauthenticated): `POST /api/v1/{orgSlug}/jobs/{jobSlug}/apply`
with multipart `payload` JSON + `resume` PDF → validate file → malware-scan hook →
create candidate + application + `ResumeDocument(QUEUED)` in one transaction →
upload bytes to private storage → enqueue `resume-processing:{org}:{resume}` →
`201 {"message": "Application received"}`. Queue failure keeps the records and
returns 503 so the resume can be re-queued.

**Authenticated job lifecycle**: Bearer JWT → verified-email gate → org context
from membership (never client input) → permission check → scoped query.
Draft → submit → approve → publish → public; invalid transitions are 409, foreign
tenants 404.

**Resume worker**: ARQ pops `process_resume`, re-scopes by `(organization_id,
resume_document_id)`, runs the guarded state machine with exponential-backoff
retries for transient failures. Current processor is a placeholder returning
`NEEDS_REVIEW`; no PDF parsing or AI exists yet.

## Data and tenancy

Every tenant-owned row carries `organization_id`; unique constraints that matter
for idempotency: `(organization_id, slug)` on jobs, `(organization_id, email)`
on candidates, `(organization_id, candidate_id, job_id)` on applications, global
`slug` on organizations, unique `storage_key` on resumes. Resume objects live at
`org/{org}/candidates/{candidate}/resumes/{resume}/original.pdf`.

## Explicitly out of scope

React, LangGraph, Groq, Resend/email delivery, search, interviews, scorecards,
offers, notifications, analytics, SSE, CI/CD, Sentry, rate limiting, security
headers, live provider deployment.
