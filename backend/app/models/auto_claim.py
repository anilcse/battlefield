from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AutoClaim(Base):
    __tablename__ = "auto_claims"
    __table_args__ = (
        UniqueConstraint("condition_id", "index_set", "model_name", name="uq_auto_claim_condition_index_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    index_set: Mapped[int] = mapped_column(index=True)
    model_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    token_id: Mapped[str] = mapped_column(String(120), default="")
    amount_redeemed: Mapped[str] = mapped_column(String(120), default="0")
    tx_hash: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    error: Mapped[str] = mapped_column(String(1000), default="")
