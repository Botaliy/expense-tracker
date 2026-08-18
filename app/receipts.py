import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.ai_client import ReceiptExtractionError, extract_receipt_data
from app.config import get_settings
from app.models import LineItem, Receipt, ReceiptStatus


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


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


def process_receipt(db: Session, receipt: Receipt) -> Receipt:
    settings = get_settings()
    image_path = settings.upload_path / receipt.image_path
    try:
        extracted = extract_receipt_data(image_path)
    except ReceiptExtractionError as exc:
        receipt.status = ReceiptStatus.FAILED
        receipt.error_message = str(exc)
        db.commit()
        db.refresh(receipt)
        return receipt

    receipt.store_name = extracted.store_name
    receipt.purchase_date = _parse_date(extracted.purchase_date)
    receipt.currency = extracted.currency
    receipt.total_amount = extracted.total_amount or sum(
        item.amount for item in extracted.items
    )
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
            )
        )

    db.commit()
    db.refresh(receipt)
    return receipt
