from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core import observability
from app.core.config import DEFAULT_JWT_SECRET, get_settings
from app.core.database import engine
from app.worker import Worker


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    observability.warn_if_rls_off(engine)
    worker = Worker() if get_settings().run_worker else None
    if worker:
        worker.start()
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.environment != "development" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set outside development")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Sales intelligence API: data import, analytics, predictions, the AI assistant and CRM sync.",
        # Off in production unless API_DOCS=true: the schema maps every endpoint for an attacker.
        openapi_url="/api/v1/openapi.json" if settings.api_docs_enabled else None,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    observability.install(app, engine)

    @app.get("/health", tags=["health"])
    def health():
        """Liveness: the process is up. /health/ready also checks the database."""
        return {"status": "ok"}

    return app


app = create_app()
