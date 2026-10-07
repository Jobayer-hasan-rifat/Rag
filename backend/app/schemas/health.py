from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "unavailable"]


class HealthInfo(BaseModel):
    status: Literal["alive"]
    app: str
    version: str
    environment: str


class LivenessStatus(BaseModel):
    status: Literal["alive"]


class ReadinessStatus(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, CheckStatus]
