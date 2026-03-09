from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.market import Market
from app.schemas.market import MarketCreate, MarketRead
from app.services.category_classifier import classify_market
from app.services.polymarket_client import PolymarketClient

router = APIRouter(prefix="/markets", tags=["markets"])
polymarket_client = PolymarketClient()


def _normalize_status(s: str) -> str:
    return (s or "open").strip().lower() or "open"


def _token_ids_from_item(item: dict) -> tuple[str | None, str | None]:
    """Extract yes_token_id, no_token_id from API item (tokens array or clobTokenIds)."""
    yes_token_id, no_token_id = None, None
    tokens = item.get("tokens") or item.get("outcomePrices")  # sometimes list of {outcome, token_id}
    if isinstance(tokens, list) and len(tokens) >= 2:
        for t in tokens:
            if isinstance(t, dict):
                outcome = (t.get("outcome") or t.get("side") or "").upper()
                tid = str(t.get("token_id") or t.get("tokenID") or "").strip()
                if tid:
                    if outcome in ("YES", "UP", "0"):
                        yes_token_id = tid
                    elif outcome in ("NO", "DOWN", "1"):
                        no_token_id = tid
        if not yes_token_id and tokens[0]:
            yes_token_id = str(tokens[0].get("token_id") or tokens[0].get("tokenID") or "").strip() or None
        if not no_token_id and len(tokens) > 1 and tokens[1]:
            no_token_id = str(tokens[1].get("token_id") or tokens[1].get("tokenID") or "").strip() or None
    clob_ids = item.get("clobTokenIds") or item.get("condition_id")  # sometimes list of 2 ids
    if isinstance(clob_ids, list) and len(clob_ids) >= 2:
        if not yes_token_id:
            yes_token_id = str(clob_ids[0]).strip() or None
        if not no_token_id:
            no_token_id = str(clob_ids[1]).strip() or None
    return yes_token_id, no_token_id


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
    payload = await polymarket_client.fetch_markets(limit=limit)
    raw_items = payload.get("data", payload if isinstance(payload, list) else [])
    synced: list[Market] = []
    for item in raw_items:
        external_id = str(item.get("id") or item.get("market_id") or "")
        title = str(item.get("question") or item.get("title") or "").strip()
        if not external_id or not title:
            continue

        existing = await db.execute(select(Market).where(Market.polymarket_market_id == external_id))
        market = existing.scalar_one_or_none()
        yes_price = float(item.get("yesPrice") or item.get("yes_price") or 0.5)
        no_price = float(item.get("noPrice") or item.get("no_price") or (1.0 - yes_price))

        description = str(item.get("description") or "")
        end_date_str = str(item.get("end_date_iso") or item.get("endDate") or "")
        category = classify_market(title, description)
        yes_token_id, no_token_id = _token_ids_from_item(item)

        if market is None:
            market = Market(
                polymarket_market_id=external_id,
                title=title,
                description=description,
                status=_normalize_status(str(item.get("status") or "open")),
                category=category,
                end_date=end_date_str,
                yes_price=yes_price,
                no_price=no_price,
                yes_token_id=yes_token_id or None,
                no_token_id=no_token_id or None,
            )
            db.add(market)
        else:
            market.title = title
            market.description = description or market.description
            market.status = _normalize_status(str(item.get("status") or market.status))
            market.category = category
            market.end_date = end_date_str or market.end_date
            market.yes_price = yes_price
            market.no_price = no_price
            if yes_token_id:
                market.yes_token_id = yes_token_id
            if no_token_id:
                market.no_token_id = no_token_id
        synced.append(market)

    await db.commit()
    for market in synced:
        await db.refresh(market)
    return synced
