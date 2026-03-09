from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ModelBudget(Base):
    __tablename__ = "model_budgets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    model_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    monthly_budget_usd: Mapped[float] = mapped_column(default=100.0)
    current_month_spent_usd: Mapped[float] = mapped_column(default=0.0)
    month_key: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
