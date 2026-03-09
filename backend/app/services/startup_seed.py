"""
On first start, when wallet/tournament start balance is 100 USD, place one test trade
(1 USD notional) per model account so each account has a live trade on record.
"""
import json
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.market import Market
from app.models.trade import Trade
from app.services.category_classifier import classify_market
from app.services.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

START_BUDGET_FOR_SEED = 100.0
TEST_TRADE_USD = 5.0


def _status_from_item(item: dict) -> str:
    """Derive an 'open' or 'closed' status from Gamma API booleans."""
    if item.get("closed"):
        return "closed"
    if item.get("active") and not item.get("closed"):
        return "open"
    if item.get("archived"):
        return "archived"
    return "open"


def _parse_json_string(val) -> list:
    """Parse a JSON-encoded string like '[\"a\",\"b\"]' into a list, or return as-is if already a list."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _token_ids_from_item(item: dict) -> tuple[str | None, str | None]:
    yes_token_id, no_token_id = None, None

    tokens = item.get("tokens")
    if isinstance(tokens, list) and len(tokens) >= 2:
        for t in tokens:
            if isinstance(t, dict):
                outcome = (t.get("outcome") or "").upper()
                tid = str(t.get("token_id") or t.get("tokenID") or "").strip()
                if tid:
                    if outcome in ("YES", "UP", "0"):
                        yes_token_id = tid
                    elif outcome in ("NO", "DOWN", "1"):
                        no_token_id = tid
        if not yes_token_id and isinstance(tokens[0], dict):
            yes_token_id = str(tokens[0].get("token_id") or tokens[0].get("tokenID") or "").strip() or None
        if not no_token_id and len(tokens) > 1 and isinstance(tokens[1], dict):
            no_token_id = str(tokens[1].get("token_id") or tokens[1].get("tokenID") or "").strip() or None

    clob_ids = _parse_json_string(item.get("clobTokenIds"))
    if len(clob_ids) >= 2:
        if not yes_token_id:
            yes_token_id = str(clob_ids[0]).strip() or None
        if not no_token_id:
            no_token_id = str(clob_ids[1]).strip() or None

    return yes_token_id, no_token_id


def _prices_from_item(item: dict) -> tuple[float, float]:
    """Extract yes/no prices from Gamma API item."""
    yes_price = float(item.get("yesPrice") or item.get("yes_price") or 0)
    no_price = float(item.get("noPrice") or item.get("no_price") or 0)
    if yes_price <= 0 and no_price <= 0:
        raw = _parse_json_string(item.get("outcomePrices"))
        if len(raw) >= 2:
            try:
                yes_price = float(raw[0])
                no_price = float(raw[1])
            except (TypeError, ValueError):
                pass
    if yes_price <= 0:
        yes_price = 0.5
    if no_price <= 0:
        no_price = round(1.0 - yes_price, 4)
    return yes_price, no_price


async def _sync_markets_once(session) -> int:
    """Fetch active markets from Polymarket Gamma API and upsert into DB. Returns count synced."""
    client = PolymarketClient()
    try:
        raw_items = await client.fetch_markets(limit=100)
    except Exception as exc:
        logger.warning("Startup seed: could not fetch markets: %s", exc)
        return 0

    if not isinstance(raw_items, list):
        raw_items = raw_items.get("data", []) if isinstance(raw_items, dict) else []

    count = 0
    for item in raw_items:
        external_id = str(item.get("id") or item.get("conditionId") or item.get("condition_id") or "")
        title = str(item.get("question") or item.get("title") or "").strip()
        if not external_id or not title:
            continue

        existing = await session.execute(select(Market).where(Market.polymarket_market_id == external_id))
        market = existing.scalar_one_or_none()

        yes_price, no_price = _prices_from_item(item)
        description = str(item.get("description") or "")
        end_date_str = str(item.get("endDateIso") or item.get("endDate") or item.get("end_date_iso") or "")
        category = classify_market(title, description)
        yes_token_id, no_token_id = _token_ids_from_item(item)
        status = _status_from_item(item)

        if market is None:
            market = Market(
                polymarket_market_id=external_id,
                title=title,
                description=description,
                status=status,
                category=category,
                end_date=end_date_str,
                yes_price=yes_price,
                no_price=no_price,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
            session.add(market)
            count += 1
        else:
            market.title = title
            market.description = description or market.description
            market.status = status
            market.category = category
            market.end_date = end_date_str or market.end_date
            market.yes_price = yes_price
            market.no_price = no_price
            if yes_token_id:
                market.yes_token_id = yes_token_id
            if no_token_id:
                market.no_token_id = no_token_id

    await session.commit()
    logger.info("Market sync: %d new, %d total from API", count, len(raw_items))
    return len(raw_items)


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
        synced = await _sync_markets_once(session)
        logger.info("Synced %d markets from Polymarket", synced)

        result = await session.execute(
            select(Market).where(func.lower(Market.status) == "open").order_by(Market.id.asc()).limit(1)
        )
        market = result.scalar_one_or_none()
        if not market:
            total = await session.execute(select(func.count()).select_from(Market))
            total_count = total.scalar() or 0
            statuses = await session.execute(select(Market.status, func.count()).group_by(Market.status))
            status_map = {row[0]: row[1] for row in statuses.all()}
            logger.warning(
                "Startup seed: no open market after sync; total markets in DB: %d, statuses: %s",
                total_count, status_map,
            )
            return

        price = float(market.yes_price)
        if price <= 0:
            price = 0.5
        quantity = max(5.0, TEST_TRADE_USD / price)

        polymarket = PolymarketClient()
        for model_name in settings.model_names:
            try:
                token_id = market.yes_token_id or None
                remote = await polymarket.place_order(
                    model_name=model_name,
                    market_id=market.polymarket_market_id,
                    side="YES",
                    quantity=quantity,
                    price=price,
                    token_id=token_id,
                )
                trade = Trade(
                    market_id=market.id,
                    model_name=model_name,
                    side="YES",
                    quantity=quantity,
                    price=price,
                    status=remote.get("status", "submitted"),
                    source=remote.get("source", "paper"),
                    external_order_id=remote.get("external_order_id", ""),
                )
                session.add(trade)
                await session.commit()
                logger.info("Startup seed: placed 1 USD test trade for model=%s market=%s", model_name, market.title[:40])
            except Exception as exc:
                logger.warning("Startup seed: test trade failed for model=%s: %s", model_name, exc)
