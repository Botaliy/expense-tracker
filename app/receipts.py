import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai_client import ReceiptExtractionError, extract_receipt_data
from app.categories import normalize_category
from app.config import get_settings
from app.models import LineItem, Receipt, ReceiptStatus
from app.products import known_products, known_stores, normalize_product
from app.schemas import ExtractedReceipt


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _total_adjustment(extracted: ExtractedReceipt) -> LineItem | None:
    """Line item covering the gap between the printed total and the items.

    Receipt-level discounts (loyalty card, rounding) show up only in the total,
    and the model occasionally misses an item. Either way the printed total is
    what was actually paid, so the difference is kept as an explicit line the
    user can see and fix, rather than silently dropped.
    """
    if extracted.total_amount is None or not extracted.items:
        return None
    diff = round(extracted.total_amount - sum(i.amount for i in extracted.items), 2)
    if abs(diff) < 0.01:
        return None
    largest = max(extracted.items, key=lambda i: i.amount)
    return LineItem(
        description="Скидка" if diff < 0 else "Корректировка",
        amount=diff,
        category=largest.category,
    )


def save_upload(filename: str, content: bytes) -> Path:
    settings = get_settings()
    suffix = Path(filename).suffix or ".jpg"
    dest = settings.upload_path / f"{uuid.uuid4().hex}{suffix}"
    dest.write_bytes(content)
    return dest


def create_receipt(db: Session, image_path: Path) -> Receipt:
    # Stored relative to the upload directory (which is served at /uploads).
    receipt = Receipt(image_path=image_path.name, status=ReceiptStatus.PENDING)
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def create_manual_expense(
    db: Session,
    description: str,
    amount: float,
    category: str,
    purchase_date: date | None,
    store_name: str | None = None,
    product: str | None = None,
) -> Receipt:
    """Record an expense entered by hand, with no receipt photo attached."""
    receipt = Receipt(
        image_path=None,
        status=ReceiptStatus.PROCESSED,
        store_name=store_name or None,
        purchase_date=purchase_date,
        total_amount=amount,
    )
    receipt.items.append(
        LineItem(
            description=description,
            amount=amount,
            category=normalize_category(category),
            product=normalize_product(product),
        )
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def process_receipt(db: Session, receipt: Receipt) -> Receipt:
    settings = get_settings()
    image_path = settings.upload_path / receipt.image_path
    try:
        extracted = extract_receipt_data(image_path, known_products(db), receipt.id, known_stores(db))
    except ReceiptExtractionError as exc:
        receipt.status = ReceiptStatus.FAILED
        receipt.error_message = str(exc)
        db.commit()
        db.refresh(receipt)
        return receipt

    receipt.store_name = extracted.store_name
    receipt.purchase_date = _parse_date(extracted.purchase_date)
    receipt.currency = extracted.currency
    receipt.raw_ai_response = extracted.model_dump_json()
    receipt.status = ReceiptStatus.PROCESSED
    receipt.error_message = None

    receipt.items.clear()
    for item in extracted.items:
        receipt.items.append(
            LineItem(
                description=item.description,
                amount=item.amount,
                quantity=item.quantity,
                category=item.category,
                product=item.product,
            )
        )

    adjustment = _total_adjustment(extracted)
    if adjustment is not None:
        receipt.items.append(adjustment)

    # Totals are always the sum of line items, so the receipt and the dashboard
    # (which aggregates line items) never disagree.
    receipt.total_amount = round(sum(item.amount for item in receipt.items), 2)

    db.commit()
    db.refresh(receipt)
    return receipt


def process_receipt_in_background(receipt_id: int) -> None:
    """Run recognition after the upload response has been sent.

    Uses its own session: the request's one is closed by then.
    """
    from app import database

    with database.SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        if receipt is not None:
            process_receipt(db, receipt)


def fail_interrupted_receipts(db: Session) -> None:
    """Receipts left pending by a restart will never finish — let them be retried."""
    for receipt in db.query(Receipt).filter(Receipt.status == ReceiptStatus.PENDING):
        receipt.status = ReceiptStatus.FAILED
        receipt.error_message = "Распознавание прервалось, попробуйте ещё раз"
    db.commit()
