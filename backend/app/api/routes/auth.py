from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.responses import envelope
from app.dependencies import AuthContextDep, AuthServiceDep, ClientIp, CurrentUser
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from app.schemas.common import ErrorResponse, ResponseEnvelope

router = APIRouter(prefix="/auth", tags=["auth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_UNAUTHENTICATED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid credentials"}
}
_RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    429: {"model": ErrorResponse, "description": "Too many attempts"}
}


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a user account",
    responses={
        409: {"model": ErrorResponse, "description": "Registration could not be completed"},
        **_RATE_LIMITED,
    },
)
async def register(
    body: RegisterRequest, request: Request, service: AuthServiceDep, client_ip: ClientIp
) -> ResponseEnvelope[UserResponse]:
    user = await service.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        client_ip=client_ip,
    )
    return envelope(request, UserResponse.from_user(user))


@router.post(
    "/login",
    summary="Exchange credentials for an access and refresh token",
    responses={**_UNAUTHENTICATED, **_RATE_LIMITED},
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    client_ip: ClientIp,
) -> ResponseEnvelope[TokenPair]:
    pair = await service.login(email=body.email, password=body.password, client_ip=client_ip)
    response.headers.update(_NO_STORE)
    return envelope(request, pair)


@router.post(
    "/refresh",
    summary="Rotate a refresh token and obtain a new access token",
    responses={**_UNAUTHENTICATED, **_RATE_LIMITED},
)
async def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    client_ip: ClientIp,
) -> ResponseEnvelope[TokenPair]:
    pair = await service.refresh(refresh_token=body.refresh_token, client_ip=client_ip)
    response.headers.update(_NO_STORE)
    return envelope(request, pair)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the session's refresh token and the presented access token",
    responses=_UNAUTHENTICATED,
)
async def logout(body: LogoutRequest, context: AuthContextDep, service: AuthServiceDep) -> Response:
    await service.logout(context=context, refresh_token=body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", summary="Current user", responses=_UNAUTHENTICATED)
async def me(request: Request, user: CurrentUser) -> ResponseEnvelope[UserResponse]:
    return envelope(request, UserResponse.from_user(user))
