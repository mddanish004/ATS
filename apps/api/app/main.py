from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
