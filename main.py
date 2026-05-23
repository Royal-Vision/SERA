from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.blueprints.utilities.metrics import get_metrics, start_metrics_server
from app.configs.config import settings
from app.configs.logger import get_logger
import uvicorn
logger = get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    metrics = get_metrics()
    metrics.set_environment(settings.APP_ENV)

    if settings.METRICS_ENABLED and settings.METRICS_HTTP_SERVER_ENABLED:
        try:
            started = start_metrics_server(settings.METRICS_PORT)
            if started:
                logger.info("Standalone metrics server started on port %s", settings.METRICS_PORT)
        except OSError as exc:
            logger.warning("Failed to start standalone metrics server on port %s: %s", settings.METRICS_PORT, exc)

    logger.info("API startup complete | env=%s | metrics_enabled=%s", settings.APP_ENV, settings.METRICS_ENABLED)
    yield
    logger.info("API shutdown complete")


app = FastAPI(
    title="SERA AI Backend",
    debug=settings.DEBUG,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.APP_ENV,
    }


@app.get("/metrics")
async def metrics() -> Response:
    if not settings.METRICS_ENABLED:
        return Response(status_code=404)

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    uvicorn.run("main:app", port=7540, host="0.0.0.0", reload=True)
