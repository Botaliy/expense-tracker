"""Search over line items: "how much do I spend on coffee?"."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LineItem, Receipt
from app.stats import MONTHS_NOMINATIVE, MONTHS_SHORT, effective_date, month_bounds, shift_month

SEARCH_MONTHS = 12
MAX_LISTED = 100


@dataclass
class Match:
    item_id: int
    description: str
    product: str | None
    amount: float
    quantity: float | None
    category: str
    receipt_id: int
    store_name: str | None
    currency: str | None
    day: date

    @property
    def label(self) -> str:
        """What the item is, for grouping: its product, or the receipt text if unlabelled."""
        return self.product or self.description


def _fold(text: str | None) -> str:
    """Case- and spacing-insensitive form: "KAFFEE  Crema" → "kaffee crema"."""
    return " ".join((text or "").casefold().split())


def _period_items(db: Session, start: date, end: date) -> list[Match]:
    rows = db.execute(
        select(
            LineItem.id,
            LineItem.description,
            LineItem.product,
            LineItem.amount,
            LineItem.quantity,
            LineItem.category,
            Receipt.id,
            Receipt.store_name,
            Receipt.currency,
            effective_date,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(effective_date >= start, effective_date < end)
        .order_by(effective_date.desc(), LineItem.id.desc())
    ).all()
    return [Match(*row) for row in rows]


def find_matches(db: Session, query: str, start: date, end: date) -> list[Match]:
    """Items whose product, receipt text or store contains ``query``, newest first.

    The product ("coffee beans") is what makes one query find the same thing
    across shops and languages; the receipt text and store still match too, so
    "Lidl" or a brand name works.

    Matching happens in Python with ``casefold``: SQLite's LIKE/lower only fold
    ASCII, so "KAFFEE" vs "kaffee" works there but "CAFÉ" vs "café" doesn't.
    A personal history is a few thousand rows a year, so this stays fast.
    """
    needle = _fold(query)
    if not needle:
        return []
    return [
        m for m in _period_items(db, start, end)
        if needle in _fold(m.product) or needle in _fold(m.description) or needle in _fold(m.store_name)
    ]


def known_names(db: Session, start: date, end: date) -> list[dict]:
    """Products and stores from the user's own receipts, most bought first.

    Items without a product yet fall back to their receipt text. Spellings that
    differ only in case or spacing are one name, shown the way it was written
    most often. Feeds both the suggestions and "often bought".
    """
    groups: dict[tuple[str, str], dict] = {}
    for m in _period_items(db, start, end):
        for kind, name in (("product", m.label), ("store", m.store_name)):
            key = _fold(name)
            if not key:
                continue
            group = groups.setdefault(
                (kind, key),
                {"kind": kind, "key": key, "visits": set(), "items": 0, "amount": 0.0, "spellings": {}},
            )
            group["items"] += 1
            group["visits"].add(m.receipt_id)
            group["amount"] += m.amount
            spelling = name.strip()
            group["spellings"][spelling] = group["spellings"].get(spelling, 0) + 1

    names = []
    for group in groups.values():
        spellings = group.pop("spellings")
        visits = group.pop("visits")
        items = group.pop("items")
        group["name"] = max(spellings, key=spellings.get)
        # A product is counted per purchase, a store per visit (receipt).
        group["count"] = len(visits) if group["kind"] == "store" else items
        names.append(group)
    return sorted(names, key=lambda n: (-n["count"], -n["amount"]))


def suggest(names: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Names containing the query; ones that start with it come first."""
    needle = _fold(query)
    if not needle:
        return []
    hits = [n for n in names if needle in n["key"]]
    hits.sort(key=lambda n: (not n["key"].startswith(needle), -n["count"]))
    return hits[:limit]


def search_period(today: date) -> tuple[date, date, list[tuple[int, int]]]:
    """The last ``SEARCH_MONTHS`` months including the current one."""
    first_year, first_mon = shift_month(today.year, today.month, -(SEARCH_MONTHS - 1))
    months = [shift_month(first_year, first_mon, i) for i in range(SEARCH_MONTHS)]
    start, _ = month_bounds(first_year, first_mon)
    _, end = month_bounds(today.year, today.month)
    return start, end, months


def summarize(matches: list[Match], months: list[tuple[int, int]]) -> dict:
    total = sum(m.amount for m in matches)

    per_month = {f"{y:04d}-{mo:02d}": {"total": 0.0, "count": 0} for y, mo in months}
    for m in matches:
        bucket = per_month.get(f"{m.day:%Y-%m}")
        if bucket is not None:
            bucket["total"] += m.amount
            bucket["count"] += 1
    chart = [
        {
            "key": f"{y:04d}-{mo:02d}",
            "short": MONTHS_SHORT[mo - 1],
            "name": f"{MONTHS_NOMINATIVE[mo - 1]} {y}",
            **per_month[f"{y:04d}-{mo:02d}"],
        }
        for y, mo in months
    ]

    # Average over the months since the first match, not all twelve: something
    # you started buying in June shouldn't look cheaper for the empty months before.
    active = [c for c in chart if c["total"]]
    if active:
        first_key = active[0]["key"]
        span = len([c for c in chart if c["key"] >= first_key])
        per_month_avg = total / span
    else:
        per_month_avg = 0.0

    # "coffee" matches "coffee beans" and "coffee to go": show what went in.
    products: dict[str, dict] = {}
    for m in matches:
        key = _fold(m.label)
        product = products.setdefault(key, {"name": m.label, "count": 0, "amount": 0.0})
        product["count"] += 1
        product["amount"] += m.amount

    places: dict[str, dict] = {}
    for m in matches:
        if not m.store_name:
            continue
        key = m.store_name.strip().casefold()
        place = places.setdefault(key, {"name": m.store_name.strip(), "count": 0, "amount": 0.0})
        place["count"] += 1
        place["amount"] += m.amount

    return {
        "total": total,
        "count": len(matches),
        "per_month": per_month_avg,
        "average_price": total / len(matches) if matches else 0.0,
        "chart": chart,
        "places": sorted(places.values(), key=lambda p: -p["amount"])[:3],
        "products": sorted(products.values(), key=lambda p: -p["amount"])[:8],
        "listed": matches[:MAX_LISTED],
    }
