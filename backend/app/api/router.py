from fastapi import APIRouter

from app.api.routes import auth, collections, documents, health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(collections.router)
api_v1_router.include_router(documents.router)

root_health_router = APIRouter()
root_health_router.include_router(health.router, include_in_schema=False)
