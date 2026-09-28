import enum
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReceiptStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    store_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    raw_ai_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus), default=ReceiptStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list["LineItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    description: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    # Generic English name shared across receipts ("coffee beans"); see app.products.
    product: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    receipt: Mapped[Receipt] = relationship(back_populates="items")


class ApiCall(Base):
    """One request to Claude, with what it cost. See app.usage."""

    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # None when the model isn't in the price table: tokens are still kept.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Plain column, not a foreign key: the cost stays after a receipt is deleted.
    receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
