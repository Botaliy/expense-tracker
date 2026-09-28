"""Expenses written straight into the test database, for tests about reports."""

from datetime import date


def add_expense(day: date, store: str, amount: float, category: str = "Продукты",
                product: str | None = None, quantity: float | None = None,
                source: str | None = None) -> int:
    """A processed receipt with one line item. Returns the receipt id."""
    from app.database import SessionLocal
    from app.models import LineItem, Receipt, ReceiptStatus

    with SessionLocal() as db:
        receipt = Receipt(
            status=ReceiptStatus.PROCESSED, store_name=store, purchase_date=day,
            total_amount=amount, source=source,
        )
        receipt.items.append(LineItem(
            description=product or store, amount=amount, quantity=quantity, category=category, product=product,
        ))
        db.add(receipt)
        db.commit()
        return receipt.id


def session():
    from app.database import SessionLocal

    return SessionLocal()

