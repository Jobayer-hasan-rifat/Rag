from fastapi import APIRouter

from app.api.routes import auth, health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)

root_health_router = APIRouter()
root_health_router.include_router(health.router, include_in_schema=False)
