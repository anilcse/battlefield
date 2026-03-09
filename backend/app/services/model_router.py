from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.model_budget import ModelBudget
from app.services.openrouter_client import OpenRouterClient


@dataclass
class ModelOutput:
    probability_yes: float
    confidence: float
    rationale: str
    cost_usd: float
    should_trade: bool = True
    skip_reason: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


def month_key_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


async def ensure_model_budgets(db: AsyncSession) -> None:
    settings = get_settings()
    month_key = month_key_now()
    for model_name in settings.model_names:
        result = await db.execute(select(ModelBudget).where(ModelBudget.model_name == model_name))
        budget = result.scalar_one_or_none()
        if budget is None:
            db.add(
                ModelBudget(
                    model_name=model_name,
                    monthly_budget_usd=settings.default_model_monthly_budget_usd,
                    current_month_spent_usd=0.0,
                    month_key=month_key,
                )
            )
    await db.commit()


async def run_model_inference(
    db: AsyncSession,
    model_name: str,
    market_title: str,
    market_context: str,
    system_prompt: str = "",
) -> ModelOutput:
    await ensure_model_budgets(db)
    month_key = month_key_now()
    result = await db.execute(select(ModelBudget).where(ModelBudget.model_name == model_name))
    budget = result.scalar_one_or_none()
    if budget is None:
        raise ValueError(f"Unknown model: {model_name}")

    if budget.month_key != month_key:
        budget.month_key = month_key
        budget.current_month_spent_usd = 0.0

    estimated_cost = 0.02
    if budget.current_month_spent_usd + estimated_cost > budget.monthly_budget_usd:
        raise ValueError(f"Budget exceeded for model {model_name}")

    probability_yes: float
    confidence: float
    rationale: str
    actual_cost: float = estimated_cost
    raw_response: Dict[str, Any] = {}
    settings = get_settings()

    try:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
        client = OpenRouterClient()
        result_data, actual_cost = await client.forecast_market(
            model_name=model_name,
            market_title=market_title,
            market_context=market_context,
            system_prompt=system_prompt,
        )
        raw_response = result_data
        probability_yes = max(0.0, min(1.0, float(result_data.get("probability_yes", 0.5))))
        confidence = max(0.0, min(1.0, float(result_data.get("confidence", 0.5))))
        rationale = str(result_data.get("rationale", "")).strip() or f"{model_name} forecast from OpenRouter."
        should_trade = result_data.get("should_trade", True)
        if isinstance(should_trade, str):
            should_trade = should_trade.lower() in ("true", "1", "yes")
        skip_reason = str(result_data.get("skip_reason", "")).strip()
    except Exception:
        raw = hashlib.sha256(f"{model_name}|{market_title}|{market_context}".encode("utf-8")).digest()
        probability_yes = 0.05 + (raw[0] / 255.0) * 0.9
        confidence = 0.4 + (raw[1] / 255.0) * 0.6
        rationale = f"{model_name} fallback forecast (OpenRouter unavailable)."
        should_trade = True
        skip_reason = ""
        actual_cost = estimated_cost

    if budget.current_month_spent_usd + actual_cost > budget.monthly_budget_usd:
        raise ValueError(f"Budget exceeded for model {model_name}")

    budget.current_month_spent_usd += actual_cost
    await db.commit()
    await db.refresh(budget)

    return ModelOutput(
        probability_yes=round(probability_yes, 4),
        confidence=round(confidence, 4),
        rationale=rationale,
        cost_usd=round(actual_cost, 6),
        should_trade=bool(should_trade),
        skip_reason=skip_reason,
        raw_response=raw_response,
    )
