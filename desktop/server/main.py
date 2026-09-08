"""FastAPI 应用工厂。装配示例见 desktop/app.py；测试直接 create_app(AppContext)。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import session as session_api
from .api import settings, system, tasks

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(ctx) -> FastAPI:
    app = FastAPI(title="mikan-quota-downloader", version="0.2.0")
    app.state.ctx = ctx
    app.include_router(system.router)
    app.include_router(tasks.router)
    app.include_router(session_api.router)
    app.include_router(settings.router)
    if _STATIC_DIR.exists():  # M3 将替换为 Vue 构建产物
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
    return app
