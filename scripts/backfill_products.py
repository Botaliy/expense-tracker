"""Give a product name to line items recorded before products existed.

Sends only the item text and shop name (no photos) to Claude, in batches, and
feeds each batch's results into the vocabulary for the next one, so labels
stay consistent across the whole history. Safe to re-run: only items without
a product are touched.

Usage:
    uv run python scripts/backfill_products.py            # label everything
    uv run python scripts/backfill_products.py --dry-run  # show, don't save

On the server:
    docker compose exec app uv run python scripts/backfill_products.py
"""

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.ai_client import ExpenseCategorizationError, label_products  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import LineItem, Receipt  # noqa: E402
from app.products import known_products  # noqa: E402

BATCH_SIZE = 80


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    init_db()  # adds the product column to an older database

    with SessionLocal() as db:
        items = db.execute(
            select(LineItem, Receipt.store_name)
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .where(LineItem.product.is_(None))
            .order_by(LineItem.id)
        ).all()
        print(f"Items without a product: {len(items)}")

        vocabulary = known_products(db)
        labelled = 0
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start:start + BATCH_SIZE]
            try:
                products = label_products([(item.description, store) for item, store in batch], vocabulary)
            except ExpenseCategorizationError as exc:
                print(f"Stopped at item {start}: {exc}")
                break

            for (item, _), product in zip(batch, products):
                if product is None:
                    continue
                print(f"  {item.description!r:40} → {product}")
                if not dry_run:
                    item.product = product
                labelled += 1
                if product not in vocabulary:
                    vocabulary.append(product)
            if not dry_run:
                db.commit()

        print(f"{'Would label' if dry_run else 'Labelled'} {labelled} of {len(items)} items.")


if __name__ == "__main__":
    main()
