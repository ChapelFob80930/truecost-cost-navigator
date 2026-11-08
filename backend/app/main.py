"""FastAPI entry point.  Run:  cd backend && ../.venv/bin/uvicorn app.main:create_app --factory --reload"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import ref, router
from .config import Settings
from .container import Container
from .exceptions import DomainError


def create_app(container: Optional[Container] = None, settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="TrueCost API", version="0.1.0",
                  description="Ranks Buffalo-area hospitals by a breast-cancer patient's estimated out-of-pocket cost.")
    app.state.container = container or Container.build(settings)

    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def allow_private_network(request: Request, call_next):
        """Lets a website (e.g. the published Lovable app) call this API when it runs on the visitor's own PC (localhost)."""
        response = await call_next(request)
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "FairCost / TrueCost API", "docs": "/docs", "health": "/health"}

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "db": getattr(app.state.container.repo, "backend", "unknown")}

    app.include_router(router)
    app.include_router(ref)
    return app
