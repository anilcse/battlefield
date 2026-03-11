from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
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
from app.services.startup_seed import seed_test_trades_on_first_start

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables(engine)
    await seed_test_trades_on_first_start()
    start_auto_claimer()
    await start_game_engine()
    yield
    await stop_game_engine()
    stop_auto_claimer()


async def create_tables(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _add_missing_columns(db_engine)
    await _reset_eliminated_entries(db_engine)


async def _reset_eliminated_entries(db_engine: AsyncEngine) -> None:
    """Reset any ELIMINATED (rank=-1) tournament entries to active on startup."""
    try:
        async with db_engine.begin() as conn:
            await conn.execute(text("UPDATE tournament_entries SET rank = NULL WHERE rank = -1"))
    except Exception:
        pass  # Table may not exist on first run


async def _add_missing_columns(db_engine: AsyncEngine) -> None:
    """ALTER TABLE to add columns that create_all won't add to existing tables."""
    migrations: list[str] = [
        "ALTER TABLE markets ADD COLUMN IF NOT EXISTS yes_token_id VARCHAR(120)",
        "ALTER TABLE markets ADD COLUMN IF NOT EXISTS no_token_id VARCHAR(120)",
        "ALTER TABLE tournament_entries ADD COLUMN IF NOT EXISTS total_volume_usd FLOAT DEFAULT 0.0",
    ]
    async with db_engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(markets_router)
app.include_router(forecasts_router)
app.include_router(trades_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
