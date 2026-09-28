"""Spotting a receipt that was recorded twice.

Two ways it happens: the very same photo uploaded again (caught by its hash
before any model call), or a second photo of the same receipt, or a photo of a
receipt already typed in by hand. The latter look alike: same store, same day,
same total. That's a strong hint, not proof (two identical coffees on one day
are real), so it's only flagged, and the user can say "not a duplicate".
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Receipt, ReceiptStatus
from app.rules import fold
from app.stats import effective_date


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def same_photo(db: Session, digest: str) -> Receipt | None:
    return db.scalar(select(Receipt).where(Receipt.image_sha256 == digest).limit(1))


def _key(store_name: str | None, total: float | None) -> tuple[str, int] | None:
    store = fold(store_name)
    if not store or not total:
        return None
    return store, round(total * 100)


def find_duplicate(db: Session, receipt: Receipt) -> Receipt | None:
    """An earlier-looking twin of ``receipt``: same store, day and total."""
    if receipt.not_duplicate or receipt.status != ReceiptStatus.PROCESSED:
        return None
    key = _key(receipt.store_name, receipt.total_amount)
    if key is None:
        return None
    day = receipt.purchase_date or receipt.uploaded_at.date()
    candidates = db.scalars(
        select(Receipt).where(
            Receipt.id != receipt.id,
            Receipt.status == ReceiptStatus.PROCESSED,
            effective_date == day,
        )
    )
    for other in candidates:
        if not other.not_duplicate and _key(other.store_name, other.total_amount) == key:
            return other
    return None


def duplicate_ids(receipts: list[tuple[Receipt, object]]) -> set[int]:
    """Receipts in a feed that have a look-alike in the same feed.

    Takes (receipt, day) rows as the feed query returns them.
    """
    groups: dict[tuple, list[int]] = {}
    for receipt, day in receipts:
        if receipt.not_duplicate or receipt.status != ReceiptStatus.PROCESSED:
            continue
        key = _key(receipt.store_name, receipt.total_amount)
        if key is not None:
            groups.setdefault((day, *key), []).append(receipt.id)
    return {rid for ids in groups.values() if len(ids) > 1 for rid in ids}
