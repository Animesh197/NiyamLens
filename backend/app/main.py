"""FastAPI application entry point."""

from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from app import __version__
from app.config import get_settings


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


api_v1 = APIRouter(prefix="/api/v1")


@api_v1.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Liveness check. Deliberately does not touch the database."""
    return HealthResponse(status="ok", service="niyamlens-backend", version=__version__)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=f"{settings.app_name} API", version=__version__)
    application.include_router(api_v1)
    return application


app = create_app()
