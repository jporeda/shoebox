"""FastAPI application: API plus the built web UI."""

from __future__ import annotations

import logging
import os
import ipaddress
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import editlog
from .api import router
from .api_people import router as people_router
from .config import get_settings
from .db import migrate

FRONTEND_DIST = Path(os.environ.get("SHOEBOX_FRONTEND", "/app/frontend/dist"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    migrate()
    yield


LOCAL_HOSTS = ["localhost", "127.0.0.1"]
# With network access on, the home network's own names: Bonjour (.local) names and private
# addresses. A DNS-rebinding page always arrives under its own public name, so it's still refused.
LAN_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
app = FastAPI(title="Shoebox", lifespan=lifespan)
APP_HEADER = "X-Shoebox"
CLIENT_HEADER = "X-Shoebox-Client"  # a random id per browser, so Undo undoes that device's own edits
CLIENT_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
VITE_DEV_PORT = 5173  # `npm run dev` proxies /api here and keeps the page's Origin
_DEFAULT_PORTS = {"http": 80, "https": 443}


def lan_enabled() -> bool:
    return get_settings().bind not in ("127.0.0.1", "localhost", "::1", "")


def _is_allowed_host(hostname: str | None) -> bool:
    """Names this server answers to: this machine's own, plus (with network access on) the home
    network's .local names, private addresses and any names listed in SHOEBOX_HOSTS."""
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    if host in LOCAL_HOSTS:
        return True
    if not lan_enabled():
        return False
    extra = {h.strip().lower() for h in get_settings().hosts.split(",") if h.strip()}
    if host in extra or (host.endswith(".local") and host != ".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in LAN_NETWORKS)


def _is_own_origin(origin: str, request: Request) -> bool:
    """True for this app's own pages: an allowed host name on the port the browser used to reach
    us (the Host header, so a remapped Docker port still works), or the Vite dev server."""
    def port(u) -> int | None:
        return u.port if u.port is not None else _DEFAULT_PORTS.get(u.scheme)

    try:
        o = urlsplit(origin)
        o_port, own_port = port(o), port(request.url)
    except ValueError:  # malformed URL or port, in the Origin or the Host header
        return False
    if o.scheme not in _DEFAULT_PORTS or not _is_allowed_host(o.hostname):
        return False  # includes "null" (sandboxed iframes, file:// pages) and other sites
    if o.username or o.password or o.path or o.query or o.fragment:
        return False  # a browser's Origin is only scheme://host[:port]
    return o_port in (own_port, VITE_DEV_PORT)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Host check (blocks DNS rebinding), then the write checks below, then tag this request's
    edits with the browser's client id so Undo is per device."""
    try:
        hostname = request.url.hostname
    except ValueError:
        hostname = None
    if not _is_allowed_host(hostname):
        return PlainTextResponse("Invalid host header", status_code=400)
    refused = _refuse_write(request)
    if refused is not None:
        return refused
    client = request.headers.get(CLIENT_HEADER, "")
    token = editlog.CURRENT_ACTOR.set(f"client:{client}" if CLIENT_ID.fullmatch(client) else "user")
    try:
        return await call_next(request)
    finally:
        editlog.CURRENT_ACTOR.reset(token)


def _refuse_write(request: Request) -> JSONResponse | None:
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
    return None

app.include_router(router)
app.include_router(people_router)
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
