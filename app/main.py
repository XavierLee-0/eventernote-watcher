"""应用入口：FastAPI + 内嵌轮询调度。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import create_router
from .auth import AuthMiddleware
from .db import Database
from .watcher import Watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR = Path(__file__).resolve().parent / "web"

db = Database(DATA_DIR / "eventernote.db")
watcher = Watcher(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await watcher.start()
    yield
    await watcher.stop()


app = FastAPI(title="Eventernote Watcher", lifespan=lifespan)
app.add_middleware(AuthMiddleware)
app.include_router(create_router(db, watcher))


@app.get("/healthz")
async def healthz():
    return {"ok": True}

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")
