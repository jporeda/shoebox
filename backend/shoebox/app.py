"""FastAPI application: API plus the built web UI."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

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


LOCAL_HOSTS = ["localhost", "127.0.0.1"]
app = FastAPI(title="Shoebox", lifespan=lifespan)
# Only answer to the names this machine uses for itself (blocks DNS-rebinding pages).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
APP_HEADER = "X-Shoebox"
VITE_DEV_PORT = 5173  # `npm run dev` proxies /api here and keeps the page's Origin
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_own_origin(origin: str, request: Request) -> bool:
    """True for this app's own pages: localhost / 127.0.0.1 on the port the browser used to reach
    us (the Host header, so a remapped Docker port still works), or the Vite dev server."""
    def port(u) -> int | None:
        return u.port if u.port is not None else _DEFAULT_PORTS.get(u.scheme)

    try:
        o = urlsplit(origin)
        o_port, own_port = port(o), port(request.url)
    except ValueError:  # malformed URL or port, in the Origin or the Host header
        return False
    if o.scheme not in _DEFAULT_PORTS or o.hostname not in LOCAL_HOSTS:
        return False  # includes "null" (sandboxed iframes, file:// pages) and other sites
    if o.username or o.password or o.path or o.query or o.fragment:
        return False  # a browser's Origin is only scheme://host[:port]
    return o_port in (own_port, VITE_DEV_PORT)


@app.middleware("http")
async def require_app_header(request: Request, call_next):
    """A page on another site can send this server simple cross-site POSTs, but it can't add a
    custom header without a CORS preflight, which is never granted. The UI sends it on every call.

    Second layer: browsers send Origin on every non-GET request, so refuse one from any other page
    (another site, another local port, `Origin: null`). No Origin at all means a non-browser client
    (CLI, tests, curl), which is allowed."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(APP_HEADER) != "1":
            return JSONResponse({"detail": f"missing {APP_HEADER} header"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and not _is_own_origin(origin, request):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    return await call_next(request)

app.include_router(router)
app.include_router(people_router)
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
