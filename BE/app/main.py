from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.errors import AppError, app_error_handler, validation_error_handler
from app.routers import (
    auth,
    care_groups,
    doses,
    elders,
    integrations,
    invitations,
    medication_schedules,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.environment == "production" and settings.jwt_secret.startswith("development-"):
        raise RuntimeError("Set a secure JWT_SECRET before starting production")
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Care-Group-ID", "X-Telegram-Bot-Api-Secret-Token"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

api_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_prefix)
app.include_router(care_groups.router, prefix=api_prefix)
app.include_router(invitations.router, prefix=api_prefix)
app.include_router(elders.router, prefix=api_prefix)
app.include_router(medication_schedules.router, prefix=api_prefix)
app.include_router(doses.router, prefix=api_prefix)
app.include_router(integrations.router, prefix=api_prefix)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}

