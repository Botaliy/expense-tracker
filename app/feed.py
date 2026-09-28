"""View model for the home page: a month's receipts grouped by day."""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.duplicates import duplicate_ids
from app.models import LineItem, Receipt, ReceiptStatus
from app.stats import effective_date


@dataclass
class FeedEntry:
    receipt: Receipt
    day: date
    amount: float
    # Categories by amount spent, largest first — the first one sets the icon.
    categories: list[str]
    item_count: int
    first_description: str | None
    # Same store, day and total as another entry: probably recorded twice.
    is_duplicate: bool = False

    @property
    def is_manual(self) -> bool:
        return self.receipt.image_path is None

    @property
    def title(self) -> str:
        if self.receipt.store_name:
            return self.receipt.store_name
        if self.is_manual and self.first_description:
            return self.first_description
        return f"Чек #{self.receipt.id}"


@dataclass
class FeedDay:
    day: date
    entries: list[FeedEntry] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(e.amount for e in self.entries)


def month_feed(db: Session, start: date, end: date) -> list[FeedDay]:
    rows = db.execute(
        select(Receipt, effective_date)
        .where(effective_date >= start, effective_date < end)
        .order_by(effective_date.desc(), Receipt.uploaded_at.desc())
    ).all()
    if not rows:
        return []

    per_category: dict[int, list[tuple[str, float]]] = {}
    counts: dict[int, int] = {}
    first_desc: dict[int, str] = {}
    item_rows = db.execute(
        select(
            LineItem.receipt_id,
            LineItem.category,
            func.sum(LineItem.amount),
            func.count(),
            func.min(LineItem.description),
        )
        .where(LineItem.receipt_id.in_([receipt.id for receipt, _ in rows]))
        .group_by(LineItem.receipt_id, LineItem.category)
    ).all()
    for receipt_id, category, amount, count, description in item_rows:
        per_category.setdefault(receipt_id, []).append((category, amount))
        counts[receipt_id] = counts.get(receipt_id, 0) + count
        first_desc.setdefault(receipt_id, description)

    duplicates = duplicate_ids(rows)
    days: list[FeedDay] = []
    for receipt, day in rows:
        cats = sorted(per_category.get(receipt.id, []), key=lambda c: -c[1])
        amount = receipt.total_amount
        if amount is None:
            amount = sum(a for _, a in cats)
        entry = FeedEntry(
            receipt=receipt,
            day=day,
            amount=amount if receipt.status == ReceiptStatus.PROCESSED else 0.0,
            categories=[c for c, _ in cats],
            item_count=counts.get(receipt.id, 0),
            first_description=first_desc.get(receipt.id),
            is_duplicate=receipt.id in duplicates,
        )
        if not days or days[-1].day != day:
            days.append(FeedDay(day=day))
        days[-1].entries.append(entry)
    return days
