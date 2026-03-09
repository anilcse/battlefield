from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market
from app.models.trade import Trade
from app.schemas.trade import TradeCreate
from app.services.polymarket_client import PolymarketClient


class TradingEngine:
    def __init__(self) -> None:
        self.client = PolymarketClient()

    async def execute_trade(self, db: AsyncSession, order: TradeCreate) -> Trade:
        result = await db.execute(select(Market).where(Market.id == order.market_id))
        market = result.scalar_one_or_none()
        if market is None:
            raise ValueError("Market not found")

        remote = await self.client.place_order(
            model_name=order.model_name,
            market_id=market.polymarket_market_id,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
        )

        trade = Trade(
            market_id=order.market_id,
            model_name=order.model_name,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status=remote.get("status", "submitted"),
            source=remote.get("source", "live"),
            external_order_id=remote.get("external_order_id", ""),
        )
        db.add(trade)
        await db.commit()
        await db.refresh(trade)
        return trade
