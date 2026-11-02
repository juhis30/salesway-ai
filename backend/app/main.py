from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import DEFAULT_JWT_SECRET, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.environment != "development" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set outside development")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Sales intelligence API: data import, analytics and (soon) the AI copilot.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
