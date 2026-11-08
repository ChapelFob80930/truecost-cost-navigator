"""Vercel entry point: runs the FastAPI app as one serverless function.

Vercel looks for an ASGI object called `app` in this file. Everything else lives in backend/app, so local runs
(uvicorn app.main:create_app --factory) and Vercel use exactly the same code. Settings come from environment
variables set in the Vercel project (see docs/DEPLOY_VERCEL.md); there is no .env file on Vercel.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import create_app  # noqa: E402

app = create_app()
