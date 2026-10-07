from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import __version__
from app.api.responses import envelope
from app.dependencies import SettingsDep, get_health_service
from app.schemas.common import ResponseEnvelope
from app.schemas.health import HealthInfo, LivenessStatus, ReadinessStatus
from app.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Service summary")
async def health(request: Request, settings: SettingsDep) -> ResponseEnvelope[HealthInfo]:
    info = HealthInfo(
        status="alive", app=settings.app_name, version=__version__, environment=settings.app_env
    )
    return envelope(request, info)


@router.get("/live", summary="Liveness probe")
async def live(request: Request) -> ResponseEnvelope[LivenessStatus]:
    return envelope(request, LivenessStatus(status="alive"))


@router.get(
    "/ready",
    summary="Readiness probe",
    response_model=ResponseEnvelope[ReadinessStatus],
    responses={503: {"model": ResponseEnvelope[ReadinessStatus]}},
)
async def ready(
    request: Request, service: Annotated[HealthService, Depends(get_health_service)]
) -> JSONResponse:
    report = await service.readiness()
    status_code = 200 if report.status == "ready" else 503
    return JSONResponse(
        status_code=status_code, content=jsonable_encoder(envelope(request, report))
    )
