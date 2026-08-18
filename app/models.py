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
    image_path: Mapped[str] = mapped_column(String(512))
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

    receipt: Mapped[Receipt] = relationship(back_populates="items")
