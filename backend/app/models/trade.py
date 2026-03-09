from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(120), default="manual", index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(default=0.0)
    price: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    source: Mapped[str] = mapped_column(String(40), default="paper")
    external_order_id: Mapped[str] = mapped_column(String(120), default="")

    market = relationship("Market", back_populates="trades")
