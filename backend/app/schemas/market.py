from datetime import datetime

from pydantic import BaseModel


class MarketCreate(BaseModel):
    polymarket_market_id: str
    title: str
    description: str = ""
    status: str = "open"
    category: str = "other"
    end_date: str = ""
    yes_price: float = 0.5
    no_price: float = 0.5


class MarketRead(MarketCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
