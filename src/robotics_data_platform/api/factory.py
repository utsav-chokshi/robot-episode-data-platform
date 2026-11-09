"""Foundation endpoints; task and artifact endpoints arrive with their components."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from robotics_data_platform import __version__
from robotics_data_platform.database import build_engine, expected_revision, probe_database
from robotics_data_platform.settings import Settings
from robotics_data_platform.ingestion.api import router
from robotics_data_platform.analytics.api import router as dataset_router
from robotics_data_platform.planning.api import router as proposal_router
from robotics_data_platform.evaluation.api import router as evaluation_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = build_engine(settings)
    revision = expected_revision(settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Robotics Data Platform",
        version=__version__,
        description="Robotics episode metadata and verified evidence inspection. Task execution remains an explicit simulator CLI; data replay cannot execute tasks.",
        lifespan=lifespan,
    )
    app.include_router(router(engine,settings.raw_root))
    app.include_router(dataset_router(engine,settings.dataset_root))
    app.include_router(proposal_router(engine,settings.raw_root))
    app.include_router(evaluation_router(engine,settings.evaluation_root))

    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["health"])
    def readyz():
        result = probe_database(engine, revision)
        return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)

    return app
