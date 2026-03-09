from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.forecast import Forecast
from app.models.market import Market
from app.schemas.forecast import ForecastRead, ForecastRequest
from app.services.model_router import run_model_inference

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.post("", response_model=ForecastRead)
async def create_forecast(payload: ForecastRequest, db: AsyncSession = Depends(get_db)) -> Forecast:
    result = await db.execute(select(Market).where(Market.id == payload.market_id))
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    try:
        model_output = await run_model_inference(
            db=db,
            model_name=payload.model_name,
            market_title=market.title,
            market_context=payload.market_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    forecast = Forecast(
        market_id=payload.market_id,
        model_name=payload.model_name,
        probability_yes=model_output.probability_yes,
        confidence=model_output.confidence,
        rationale=model_output.rationale,
        cost_usd=model_output.cost_usd,
    )
    db.add(forecast)
    await db.commit()
    await db.refresh(forecast)
    return forecast
