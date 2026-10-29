"""FastAPI application: API plus the built web UI."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
app.include_router(router)
app.include_router(people_router)
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
