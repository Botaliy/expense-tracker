import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    # sha256 of the uploaded photo: the same file uploaded twice isn't recognized twice.
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Set when the user said a look-alike receipt isn't a duplicate. See app.duplicates.
    not_duplicate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # "bank" for expenses created from a statement import; None otherwise.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)

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


class ForecastExclusion(Base):
    """A product or a whole category the shopping forecast ignores. See app.forecast."""

    __tablename__ = "forecast_exclusions"
    __table_args__ = (UniqueConstraint("kind", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # "product" or "category"
    value: Mapped[str] = mapped_column(String(64))


class CategoryRule(Base):
    """A correction the user made once, applied to the same line next time. See app.rules."""

    __tablename__ = "category_rules"
    __table_args__ = (UniqueConstraint("key", "store_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(255))  # folded item description
    store_key: Mapped[str] = mapped_column(String(255), default="")  # folded store, "" for any
    description: Mapped[str] = mapped_column(String(255))  # as the user last saw it
    store_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    product: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class BankTransaction(Base):
    """One card payment from an imported bank statement. See app.bank_import."""

    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank: Mapped[str] = mapped_column(String(32))
    # Stable hash of the statement row, so importing the same file twice is a no-op.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    booked_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)  # money spent, positive
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str] = mapped_column(String(255))
    # card (a purchase), transfer (could be rent, could be to yourself), fee
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # pending (waiting in the preview), created, matched (an existing receipt), skipped
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # The receipt it created or was matched to; plain column so deleting a receipt
    # doesn't take the statement row with it.
    receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Suggested match shown in the preview, before the user confirms.
    match_receipt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class Budget(Base):
    """Monthly spending limit for one category. See app.budgets."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(64), unique=True)
    monthly_limit: Mapped[float] = mapped_column(Float)


class ShoppingItem(Base):
    """A line on the shopping list. See app.shopping.

    Forecast lines exist here only once ticked off (product + the due date they
    were ticked for); lines typed by hand live here from the start.
    """

    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    product: Mapped[str | None] = mapped_column(String(64), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
