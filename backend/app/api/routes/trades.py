from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.trade import Trade
from app.schemas.trade import TradeCreate, TradeRead
from app.services.trading_engine import TradingEngine

router = APIRouter(prefix="/trades", tags=["trades"])
engine = TradingEngine()


@router.get("", response_model=list[TradeRead])
async def list_trades(db: AsyncSession = Depends(get_db)) -> list[Trade]:
    result = await db.execute(select(Trade).order_by(Trade.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=TradeRead)
async def place_trade(payload: TradeCreate, db: AsyncSession = Depends(get_db)) -> Trade:
    try:
        return await engine.execute_trade(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
