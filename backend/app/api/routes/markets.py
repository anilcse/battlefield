from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.market import Market
from app.schemas.market import MarketCreate, MarketRead
from app.services.category_classifier import classify_market
from app.services.startup_seed import _sync_markets_once

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=list[MarketRead])
async def list_markets(db: AsyncSession = Depends(get_db)) -> list[Market]:
    result = await db.execute(select(Market).order_by(Market.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=MarketRead)
async def create_market(payload: MarketCreate, db: AsyncSession = Depends(get_db)) -> Market:
    existing = await db.execute(select(Market).where(Market.polymarket_market_id == payload.polymarket_market_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Market already exists")
    data = payload.model_dump()
    if not data.get("category") or data["category"] == "other":
        data["category"] = classify_market(data["title"], data.get("description", ""))
    market = Market(**data)
    db.add(market)
    await db.commit()
    await db.refresh(market)
    return market


@router.post("/sync", response_model=list[MarketRead])
async def sync_markets(limit: int = 100, db: AsyncSession = Depends(get_db)) -> list[Market]:
    """Sync active markets from Polymarket Gamma API via /events endpoint."""
    await _sync_markets_once(db)
    result = await db.execute(select(Market).order_by(Market.created_at.desc()).limit(limit))
    return list(result.scalars().all())
