from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="default")
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    duration_days: Mapped[int] = mapped_column(default=30)
    start_budget_usd: Mapped[float] = mapped_column(default=100.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TournamentEntry(Base):
    __tablename__ = "tournament_entries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tournament_id: Mapped[int] = mapped_column(index=True)
    model_name: Mapped[str] = mapped_column(String(120), index=True)
    starting_balance_usd: Mapped[float] = mapped_column(default=100.0)
    current_balance_usd: Mapped[float] = mapped_column(default=100.0)
    total_trades: Mapped[int] = mapped_column(default=0)
    total_forecasts: Mapped[int] = mapped_column(default=0)
    realized_pnl_usd: Mapped[float] = mapped_column(default=0.0)
    total_volume_usd: Mapped[float] = mapped_column(default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(default=0.0)
    rank: Mapped[Optional[int]] = mapped_column(default=None)
