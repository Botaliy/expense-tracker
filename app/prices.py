"""Prices of the things you buy again: what got dearer, and where it's cheaper.

Works on the product labels (app.products), so "ΓΑΛΑ 1L" at one shop and
"MILK FRESH 1L" at another are both "milk". The price is per unit when the
receipt gave a quantity (per kg for weighed goods), else per line.

Price changes compare a purchase with earlier ones *in the same shop*: milk at
Lidl being cheaper than at Alphamega isn't inflation. Shop comparisons use the
median price per shop, so one promotion doesn't make a shop look cheap.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LineItem, Receipt
from app.rules import fold
from app.stats import effective_date

LOOKBACK_DAYS = 365
# Smaller moves are rounding, weight differences and noise.
MIN_CHANGE = 0.10
# Earlier purchases in the same shop needed to call a price "usual".
MIN_HISTORY = 2
# Cheaper-elsewhere needs a real gap, not a few cents.
MIN_SAVING = 0.08


@dataclass
class Purchase:
    product: str
    store: str
    store_key: str
    day: date
    unit_price: float
    receipt_id: int


@dataclass
class PriceChange:
    product: str
    store: str
    last_price: float
    usual_price: float
    day: date
    receipt_id: int
    purchases: int

    @property
    def change(self) -> float:
        return self.last_price / self.usual_price - 1


@dataclass
class StorePrice:
    store: str
    price: float
    purchases: int


@dataclass
class Comparison:
    product: str
    stores: list[StorePrice]  # cheapest first
    purchases: int

    @property
    def saving(self) -> float:
        """How much cheaper the cheapest shop is than the one you use most."""
        usual = self.usual_store
        return 1 - self.stores[0].price / usual.price if usual.price else 0.0

    @property
    def usual_store(self) -> StorePrice:
        """Where it's bought most often; on a tie, the dearer one (that's the question)."""
        return max(self.stores, key=lambda s: (s.purchases, s.price))


def purchases(db: Session, today: date | None = None) -> list[Purchase]:
    today = today or date.today()
    rows = db.execute(
        select(
            LineItem.product, LineItem.amount, LineItem.quantity,
            Receipt.store_name, effective_date, Receipt.id,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(
            LineItem.product.is_not(None),
            LineItem.amount > 0,
            Receipt.store_name.is_not(None),
            effective_date >= today - timedelta(days=LOOKBACK_DAYS),
        )
        .order_by(effective_date, LineItem.id)
    ).all()
    out = []
    for product, amount, quantity, store, day, receipt_id in rows:
        unit = amount / quantity if quantity and quantity > 0 else amount
        out.append(Purchase(product, store.strip(), fold(store), day, round(unit, 4), receipt_id))
    return out


def changes(items: list[Purchase], since: date) -> list[PriceChange]:
    """Latest purchases (on or after ``since``) priced unlike before in the same shop."""
    by_key: dict[tuple[str, str], list[Purchase]] = {}
    for p in items:
        by_key.setdefault((p.product, p.store_key), []).append(p)
    out = []
    for (product, _), history in by_key.items():
        last = history[-1]
        earlier = history[:-1]
        if last.day < since or len(earlier) < MIN_HISTORY:
            continue
        usual = median(p.unit_price for p in earlier)
        if usual <= 0 or abs(last.unit_price / usual - 1) < MIN_CHANGE:
            continue
        out.append(PriceChange(
            product=product, store=last.store, last_price=last.unit_price, usual_price=usual,
            day=last.day, receipt_id=last.receipt_id, purchases=len(history),
        ))
    return sorted(out, key=lambda c: -c.change)


def comparisons(items: list[Purchase]) -> list[Comparison]:
    """Products bought in more than one shop, where the price differs enough to matter."""
    by_product: dict[str, dict[str, list[Purchase]]] = {}
    for p in items:
        by_product.setdefault(p.product, {}).setdefault(p.store_key, []).append(p)
    out = []
    for product, shops in by_product.items():
        if len(shops) < 2:
            continue
        stores = sorted(
            (
                StorePrice(store=ps[-1].store, price=median(p.unit_price for p in ps), purchases=len(ps))
                for ps in shops.values()
            ),
            key=lambda s: s.price,
        )
        comparison = Comparison(product=product, stores=stores, purchases=sum(s.purchases for s in stores))
        if comparison.saving >= MIN_SAVING:
            out.append(comparison)
    # What's bought often and could be much cheaper matters most.
    return sorted(out, key=lambda c: -(c.saving * c.purchases))


def report(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    items = purchases(db, today)
    found = changes(items, since=today - timedelta(days=60))
    return {
        "up": [c for c in found if c.change > 0],
        "down": sorted((c for c in found if c.change < 0), key=lambda c: c.change),
        "cheaper": comparisons(items),
        "tracked": len({p.product for p in items}),
    }
