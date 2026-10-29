"""FastAPI application: API plus the built web UI."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import router
from .api_people import router as people_router
from .db import migrate

FRONTEND_DIST = Path(os.environ.get("SHOEBOX_FRONTEND", "/app/frontend/dist"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    migrate()
    yield


app = FastAPI(title="Shoebox", lifespan=lifespan)
# Only answer to the names this machine uses for itself (blocks DNS-rebinding pages).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
APP_HEADER = "X-Shoebox"


@app.middleware("http")
async def require_app_header(request: Request, call_next):
    """A page on another site can send this server simple cross-site POSTs, but it can't add a
    custom header without a CORS preflight, which is never granted. The UI sends it on every call."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(APP_HEADER) != "1":
        return JSONResponse({"detail": f"missing {APP_HEADER} header"}, status_code=403)
    return await call_next(request)

app.include_router(router)
app.include_router(people_router)
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
