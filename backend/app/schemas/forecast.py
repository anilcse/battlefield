from datetime import datetime

from pydantic import BaseModel, Field


class ForecastRequest(BaseModel):
    model_name: str
    market_id: int
    market_context: str = ""


class ForecastRead(BaseModel):
    id: int
    market_id: int
    model_name: str
    probability_yes: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    cost_usd: float
    created_at: datetime

    model_config = {"from_attributes": True}
