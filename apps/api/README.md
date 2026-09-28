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
