"""Точка входа FastAPI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routes import admin, oauth, public

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logging.getLogger("autoroll").info(
        "DB ready: %s", settings.database_url.split("@")[-1]
    )
    yield


app = FastAPI(
    title="AutoRoll License Server",
    version="1.0.0",
    description="Выдача ключей, проверка HWID, kill switch и события.",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(public.router)
app.include_router(oauth.router)
app.include_router(admin.router)


@app.get("/")
def index() -> dict:
    return {
        "service": "AutoRoll license server",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
