from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    polymarket_market_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="open")
    category: Mapped[str] = mapped_column(String(60), default="other", index=True)
    end_date: Mapped[str] = mapped_column(String(30), default="")
    yes_price: Mapped[float] = mapped_column(default=0.5)
    no_price: Mapped[float] = mapped_column(default=0.5)

    forecasts = relationship("Forecast", back_populates="market", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="market", cascade="all, delete-orphan")
