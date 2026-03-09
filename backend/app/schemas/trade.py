from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TradeCreate(BaseModel):
    market_id: int
    model_name: str = "manual"
    side: Literal["YES", "NO"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0, le=1)


class TradeRead(BaseModel):
    id: int
    market_id: int
    model_name: str
    side: str
    quantity: float
    price: float
    status: str
    source: str
    external_order_id: str
    created_at: datetime

    model_config = {"from_attributes": True}
