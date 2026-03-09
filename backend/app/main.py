from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.routes.admin import router as admin_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.forecasts import router as forecasts_router
from app.api.routes.health import router as health_router
from app.api.routes.markets import router as markets_router
from app.api.routes.trades import router as trades_router
from app.core.config import get_settings
from app.db import base as _db_models  # noqa: F401
from app.db.session import engine
from app.models.base import Base
from app.services.auto_claimer import start_auto_claimer, stop_auto_claimer
from app.services.game_engine import start_game_engine, stop_game_engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables(engine)
    start_auto_claimer()
    await start_game_engine()
    yield
    await stop_game_engine()
    stop_auto_claimer()


async def create_tables(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(health_router)
app.include_router(markets_router)
app.include_router(forecasts_router)
app.include_router(trades_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
