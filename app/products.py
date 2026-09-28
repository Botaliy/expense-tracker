"""Product names: one short English label per line item, shared across receipts.

A receipt says "KAFFEE CREMA 1KG" or "CAFE SOLO"; the product is "coffee beans"
or "coffee". Search and "often bought" work on these, so a query finds the same
thing whatever language or abbreviation the shop printed.

The model is shown the existing vocabulary and told to reuse it, so labels
converge instead of drifting ("coffee to go" vs "takeaway coffee").
"""

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LineItem

MAX_PRODUCT_LENGTH = 64
# How much of the vocabulary goes into a prompt: the most used labels are the
# ones worth matching, and this keeps the prompt small (~1–2k tokens).
VOCABULARY_SIZE = 300

PRODUCT_GUIDANCE = (
    "Also give each item a 'product': a short generic English name for what it is, "
    "the way you'd search for it later — lowercase, 1–3 words, no brands, sizes, "
    "flavours or quantities. Put the general word first so related products share "
    "it: 'coffee beans', 'coffee to go', 'beer', 'cat food', 'cat litter', 'fuel', "
    "'parking', 'haircut'. Reuse a name from the known products list whenever it "
    "fits, even if the wording differs; only invent a new one when none fits."
)


def normalize_product(value: str | None) -> str | None:
    """Lowercase, single-spaced, trimmed of stray punctuation; None if empty."""
    if not value:
        return None
    text = " ".join(value.casefold().split())
    text = re.sub(r"^[\W_]+|[\W_]+$", "", text)
    return text[:MAX_PRODUCT_LENGTH] or None


def known_products(db: Session, limit: int = VOCABULARY_SIZE) -> list[str]:
    """Product names already in use, most frequent first."""
    return list(
        db.scalars(
            select(LineItem.product)
            .where(LineItem.product.is_not(None))
            .group_by(LineItem.product)
            .order_by(func.count().desc(), LineItem.product)
            .limit(limit)
        )
    )


def vocabulary_prompt(products: list[str]) -> str:
    if not products:
        return "Known products: none yet."
    return "Known products (reuse when they fit): " + ", ".join(products) + "."
