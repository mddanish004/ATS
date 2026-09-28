from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.public_careers import router as public_careers_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(jobs_router)
api_router.include_router(organizations_router)
api_router.include_router(public_careers_router)
