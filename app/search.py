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
    amount: float
    quantity: float | None
    category: str
    receipt_id: int
    store_name: str | None
    currency: str | None
    day: date
    matched: str  # the term that matched, to show why a row is here


def find_matches(db: Session, terms: list[str], start: date, end: date) -> list[Match]:
    """Items whose description or store contains any of ``terms``, newest first.

    Matching happens in Python with ``casefold``: SQLite's LIKE/lower only fold
    ASCII, so "KAFFEE" vs "kaffee" works there but "CAFÉ" vs "café" doesn't.
    A personal history is a few thousand rows a year, so this stays fast.
    """
    folded = [t.casefold() for t in terms if t.strip()]
    if not folded:
        return []
    rows = db.execute(
        select(
            LineItem.id,
            LineItem.description,
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

    matches = []
    for item_id, desc, amount, qty, category, receipt_id, store, currency, day in rows:
        haystacks = (desc.casefold(), (store or "").casefold())
        term = next((t for t in folded if any(t in h for h in haystacks)), None)
        if term is not None:
            matches.append(
                Match(item_id, desc, amount, qty, category, receipt_id, store, currency, day, term)
            )
    return matches


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
        "listed": matches[:MAX_LISTED],
    }
