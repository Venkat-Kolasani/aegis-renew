"""FastAPI application factory for Aegis."""

from fastapi import FastAPI

from backend.routes import agent, detection, payments


def create_app() -> FastAPI:
    """Create the Aegis API with its Phase 0 route modules."""
    app = FastAPI(title="Aegis API", version="0.1.0")

    @app.get("/health")
    def health_check() -> dict[str, str]:
        """Return the API health status."""
        return {"status": "ok"}

    app.include_router(detection.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(payments.router, prefix="/api")
    return app


app = create_app()
