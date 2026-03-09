from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(120), index=True)
    probability_yes: Mapped[float] = mapped_column(index=True)
    confidence: Mapped[float] = mapped_column(default=0.5)
    rationale: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(default=0.0)

    market = relationship("Market", back_populates="forecasts")
