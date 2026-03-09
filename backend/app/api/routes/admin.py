import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.auto_claim import AutoClaim
from app.models.forecast import Forecast
from app.models.market import Market
from app.models.model_budget import ModelBudget
from app.models.tournament import Tournament, TournamentEntry
from app.models.trade import Trade
from app.schemas.trade import TradeCreate, TradeRead
from app.services.model_router import ensure_model_budgets
from app.services.trading_engine import TradingEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _compute_model_portfolios(trades: list[Trade], market_map: dict[int, Market]) -> list[dict]:
    model_stats: dict[str, dict] = {}
    for trade in trades:
        model_name = trade.model_name or "manual"
        market = market_map.get(trade.market_id)
        if market is None:
            continue
        side = (trade.side or "").upper()
        qty = float(trade.quantity)
        price = float(trade.price)
        notional = qty * price

        stats = model_stats.setdefault(
            model_name,
            {
                "model_name": model_name,
                "trade_count": 0,
                "volume_usd": 0.0,
                "cash_spent_usd": 0.0,
                "yes_exposure_qty": 0.0,
                "no_exposure_qty": 0.0,
                "mark_to_market_pnl_usd": 0.0,
            },
        )
        stats["trade_count"] += 1
        stats["volume_usd"] += notional

        if side == "YES":
            stats["cash_spent_usd"] += notional
            stats["yes_exposure_qty"] += qty
            stats["mark_to_market_pnl_usd"] += qty * (float(market.yes_price) - price)
        elif side == "NO":
            stats["cash_spent_usd"] += notional
            stats["no_exposure_qty"] += qty
            stats["mark_to_market_pnl_usd"] += qty * (float(market.no_price) - price)

    return [
        {
            "model_name": row["model_name"],
            "trade_count": row["trade_count"],
            "volume_usd": round(row["volume_usd"], 4),
            "cash_spent_usd": round(row["cash_spent_usd"], 4),
            "yes_exposure_qty": round(row["yes_exposure_qty"], 4),
            "no_exposure_qty": round(row["no_exposure_qty"], 4),
            "mark_to_market_pnl_usd": round(row["mark_to_market_pnl_usd"], 4),
        }
        for row in sorted(model_stats.values(), key=lambda x: x["mark_to_market_pnl_usd"], reverse=True)
    ]


@router.get("/model-budgets")
async def list_model_budgets(db: AsyncSession = Depends(get_db)) -> list[dict]:
    await ensure_model_budgets(db)
    result = await db.execute(select(ModelBudget).order_by(ModelBudget.model_name.asc()))
    records = result.scalars().all()
    current_month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    output = []
    for row in records:
        spent = row.current_month_spent_usd if row.month_key == current_month else 0.0
        output.append(
            {
                "model_name": row.model_name,
                "month_key": current_month,
                "monthly_budget_usd": row.monthly_budget_usd,
                "current_month_spent_usd": spent,
                "remaining_usd": max(0.0, row.monthly_budget_usd - spent),
            }
        )
    return output


@router.get("/auto-claims")
async def list_auto_claims(limit: int = 100, db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(AutoClaim).order_by(AutoClaim.created_at.desc()).limit(limit))
    rows = result.scalars().all()
    return [
        {
            "condition_id": row.condition_id,
            "index_set": row.index_set,
            "model_name": getattr(row, "model_name", "") or "",
            "token_id": row.token_id,
            "amount_redeemed": row.amount_redeemed,
            "tx_hash": row.tx_hash,
            "status": row.status,
            "error": row.error,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/analytics")
async def trading_analytics(db: AsyncSession = Depends(get_db)) -> dict:
    trades_result = await db.execute(select(Trade).order_by(Trade.created_at.asc()))
    markets_result = await db.execute(select(Market))
    forecasts_result = await db.execute(select(Forecast))

    trades = list(trades_result.scalars().all())
    markets = list(markets_result.scalars().all())
    forecasts = list(forecasts_result.scalars().all())
    market_map = {m.id: m for m in markets}

    total_volume = 0.0
    mtm_pnl = 0.0
    side_counts = {"YES": 0, "NO": 0}
    source_counts: dict[str, int] = {}
    daily_volume: dict[str, float] = {}
    market_volume: dict[str, float] = {}

    for trade in trades:
        notional = float(trade.quantity) * float(trade.price)
        total_volume += notional
        side = (trade.side or "").upper()
        if side in side_counts:
            side_counts[side] += 1
        source_counts[trade.source] = source_counts.get(trade.source, 0) + 1

        day = trade.created_at.date().isoformat()
        daily_volume[day] = daily_volume.get(day, 0.0) + notional

        market = market_map.get(trade.market_id)
        market_name = market.title if market else f"Market {trade.market_id}"
        market_volume[market_name] = market_volume.get(market_name, 0.0) + notional
        if market is not None:
            if side == "YES":
                mtm_pnl += float(trade.quantity) * (float(market.yes_price) - float(trade.price))
            elif side == "NO":
                mtm_pnl += float(trade.quantity) * (float(market.no_price) - float(trade.price))

    model_forecasts: dict[str, int] = {}
    model_avg_conf: dict[str, float] = {}
    for forecast in forecasts:
        name = forecast.model_name
        model_forecasts[name] = model_forecasts.get(name, 0) + 1
        model_avg_conf[name] = model_avg_conf.get(name, 0.0) + float(forecast.confidence)
    for key, count in model_forecasts.items():
        model_avg_conf[key] = model_avg_conf[key] / count if count else 0.0

    model_portfolios = _compute_model_portfolios(trades, market_map)
    model_portfolio_map = {row["model_name"]: row for row in model_portfolios}
    model_names = sorted(set(model_forecasts.keys()) | set(model_portfolio_map.keys()))

    top_markets = sorted(market_volume.items(), key=lambda item: item[1], reverse=True)[:8]
    daily_points = sorted(daily_volume.items())[-14:]

    return {
        "overview": {
            "total_trades": len(trades),
            "total_forecasts": len(forecasts),
            "total_markets": len(markets),
            "total_volume_usd": round(total_volume, 4),
            "mark_to_market_pnl_usd": round(mtm_pnl, 4),
            "yes_trades": side_counts["YES"],
            "no_trades": side_counts["NO"],
            "avg_forecast_confidence": round(
                (sum(float(f.confidence) for f in forecasts) / len(forecasts)) if forecasts else 0.0,
                4,
            ),
        },
        "daily_volume": [{"date": date, "volume_usd": round(volume, 4)} for date, volume in daily_points],
        "top_markets_by_volume": [{"market": name, "volume_usd": round(volume, 4)} for name, volume in top_markets],
        "trade_sources": [{"source": source, "count": count} for source, count in source_counts.items()],
        "models": [
            {
                "model_name": name,
                "forecast_count": model_forecasts.get(name, 0),
                "avg_confidence": round(model_avg_conf.get(name, 0.0), 4),
                "trade_count": model_portfolio_map.get(name, {}).get("trade_count", 0),
                "volume_usd": model_portfolio_map.get(name, {}).get("volume_usd", 0.0),
                "mark_to_market_pnl_usd": model_portfolio_map.get(name, {}).get("mark_to_market_pnl_usd", 0.0),
            }
            for name in model_names
        ],
    }


@router.get("/model-portfolios")
async def model_portfolios(db: AsyncSession = Depends(get_db)) -> list[dict]:
    trades_result = await db.execute(select(Trade).order_by(Trade.created_at.asc()))
    markets_result = await db.execute(select(Market))
    trades = list(trades_result.scalars().all())
    markets = list(markets_result.scalars().all())
    market_map = {m.id: m for m in markets}
    return _compute_model_portfolios(trades, market_map)


@router.get("/leaderboard")
async def leaderboard(db: AsyncSession = Depends(get_db)) -> dict:
    t_result = await db.execute(
        select(Tournament).where(Tournament.status.in_(["active", "completed"])).order_by(Tournament.created_at.desc())
    )
    tournament = t_result.scalar_one_or_none()
    if tournament is None:
        return {"tournament": None, "entries": []}

    e_result = await db.execute(
        select(TournamentEntry)
        .where(TournamentEntry.tournament_id == tournament.id)
        .order_by(TournamentEntry.current_balance_usd.desc())
    )
    entries = list(e_result.scalars().all())

    now = datetime.now(tz=timezone.utc)
    elapsed = (now - tournament.started_at).total_seconds()
    total = (tournament.ends_at - tournament.started_at).total_seconds()
    progress_pct = min(100.0, max(0.0, (elapsed / total) * 100.0)) if total > 0 else 0.0

    return {
        "tournament": {
            "id": tournament.id,
            "name": tournament.name,
            "status": tournament.status,
            "started_at": tournament.started_at,
            "ends_at": tournament.ends_at,
            "duration_days": tournament.duration_days,
            "start_budget_usd": tournament.start_budget_usd,
            "progress_pct": round(progress_pct, 1),
        },
        "entries": [
            {
                "rank": idx + 1,
                "model_name": entry.model_name,
                "starting_balance_usd": entry.starting_balance_usd,
                "current_balance_usd": round(entry.current_balance_usd, 4),
                "total_return_pct": round(
                    ((entry.current_balance_usd - entry.starting_balance_usd) / entry.starting_balance_usd) * 100.0,
                    2,
                )
                if entry.starting_balance_usd > 0
                else 0.0,
                "total_trades": entry.total_trades,
                "total_forecasts": entry.total_forecasts,
                "realized_pnl_usd": round(entry.realized_pnl_usd, 4),
                "unrealized_pnl_usd": round(entry.unrealized_pnl_usd, 4),
            }
            for idx, entry in enumerate(entries)
        ],
    }


@router.post("/test-trade", response_model=TradeRead)
async def test_trade(
    db: AsyncSession = Depends(get_db),
    market_id: int | None = Query(None, description="Market ID (internal). If omitted, first open market is used."),
    model_name: str | None = Query(None, description="Model name. If omitted, first configured model is used."),
    side: str = Query("YES", description="YES or NO"),
    quantity: float | None = Query(None, description="Quantity. Ignored if usd_value is set."),
    usd_value: float | None = Query(None, gt=0, description="Target notional in USD (e.g. 1 for 1 USD). Overrides quantity."),
) -> Trade:
    """Place a single test trade. Uses first open market and first model if not specified."""
    settings = get_settings()
    model = model_name or (settings.model_names[0] if settings.model_names else None)
    if not model:
        raise HTTPException(status_code=400, detail="No model configured; set MODEL_NAMES in .env")

    if market_id is not None:
        result = await db.execute(select(Market).where(Market.id == market_id))
        market = result.scalar_one_or_none()
        if not market:
            raise HTTPException(status_code=404, detail=f"Market {market_id} not found")
    else:
        result = await db.execute(select(Market).where(Market.status == "open").order_by(Market.id.asc()).limit(1))
        market = result.scalar_one_or_none()
        if not market:
            raise HTTPException(
                status_code=400,
                detail="No open market found. Sync markets first: POST /markets/sync",
            )

    price = float(market.yes_price) if side.upper() == "YES" else float(market.no_price)
    if side.upper() not in ("YES", "NO"):
        raise HTTPException(status_code=400, detail="side must be YES or NO")

    if usd_value is not None:
        quantity = usd_value / price if price > 0 else 0.1
    elif quantity is None or quantity <= 0:
        quantity = 0.1
    if quantity <= 0 or quantity > 10000:
        raise HTTPException(status_code=400, detail="Computed quantity out of range")

    engine = TradingEngine()
    order = TradeCreate(
        market_id=market.id,
        model_name=model,
        side=side.upper(),
        quantity=quantity,
        price=price,
    )
    try:
        logger.info("Test trade: market_id=%s model=%s side=%s qty=%s price=%s", market.id, model, side, quantity, price)
        trade = await engine.execute_trade(db, order)
        return trade
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
