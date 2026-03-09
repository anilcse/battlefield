"""
On first start, when wallet/tournament start balance is 100 USD, place one test trade
(1 USD notional) per model account so each account has a live trade on record.
"""
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.market import Market
from app.models.trade import Trade
from app.schemas.trade import TradeCreate
from app.services.category_classifier import classify_market
from app.services.polymarket_client import PolymarketClient
from app.services.trading_engine import TradingEngine

logger = logging.getLogger(__name__)

START_BUDGET_FOR_SEED = 100.0
TEST_TRADE_USD = 1.0


async def _sync_markets_once(session) -> None:
    """Fetch markets from Polymarket and upsert into DB."""
    client = PolymarketClient()
    try:
        payload = await client.fetch_markets(limit=25)
    except Exception as exc:
        logger.warning("Startup seed: could not fetch markets: %s", exc)
        return
    raw_items = payload.get("data", payload if isinstance(payload, list) else [])
    for item in raw_items:
        external_id = str(item.get("id") or item.get("market_id") or "")
        title = str(item.get("question") or item.get("title") or "").strip()
        if not external_id or not title:
            continue
        existing = await session.execute(select(Market).where(Market.polymarket_market_id == external_id))
        market = existing.scalar_one_or_none()
        yes_price = float(item.get("yesPrice") or item.get("yes_price") or 0.5)
        no_price = float(item.get("noPrice") or item.get("no_price") or (1.0 - yes_price))
        description = str(item.get("description") or "")
        end_date_str = str(item.get("end_date_iso") or item.get("endDate") or "")
        category = classify_market(title, description)
        if market is None:
            market = Market(
                polymarket_market_id=external_id,
                title=title,
                description=description,
                status=str(item.get("status") or "open"),
                category=category,
                end_date=end_date_str,
                yes_price=yes_price,
                no_price=no_price,
            )
            session.add(market)
        else:
            market.title = title
            market.description = description or market.description
            market.status = str(item.get("status") or market.status)
            market.category = category
            market.end_date = end_date_str or market.end_date
            market.yes_price = yes_price
            market.no_price = no_price
    await session.commit()


async def seed_test_trades_on_first_start() -> None:
    """
    If no trades exist and tournament start budget is 100 USD, sync markets
    and place one 1 USD test trade per model account.
    """
    settings = get_settings()
    if settings.tournament_start_budget_usd != START_BUDGET_FOR_SEED:
        return

    async with SessionLocal() as session:
        result = await session.execute(select(func.count()).select_from(Trade))
        count = result.scalar() or 0
        if count > 0:
            return

        logger.info("First start with 100 USD start budget: seeding one test trade per model account")
        await _sync_markets_once(session)

        result = await session.execute(
            select(Market).where(Market.status == "open").order_by(Market.id.asc()).limit(1)
        )
        market = result.scalar_one_or_none()
        if not market:
            logger.warning("Startup seed: no open market after sync; skipping test trades")
            return

        price = float(market.yes_price)
        if price <= 0:
            price = 0.5
        quantity = TEST_TRADE_USD / price
        engine = TradingEngine()

        models_with_account = [
            name
            for name in settings.model_names
            if (settings.get_model_account(name).get("private_key") or "").strip()
        ]
        if not models_with_account:
            logger.warning("Startup seed: no model has private_key; skipping test trades")
            return

        for model_name in models_with_account:
            try:
                order = TradeCreate(
                    market_id=market.id,
                    model_name=model_name,
                    side="YES",
                    quantity=quantity,
                    price=price,
                )
                await engine.execute_trade(session, order)
                logger.info("Startup seed: placed 1 USD test trade for model=%s market_id=%s", model_name, market.id)
            except Exception as exc:
                logger.warning("Startup seed: test trade failed for model=%s: %s", model_name, exc)
