"""Regular payments (rent, phone, subscriptions) and whether this month's has come.

Nothing is added automatically: payments arrive as receipts or statement
imports like everything else. This only notices the pattern and reminds when a
month is missing one.

A payment is regular when it shows up about once a month, around the same day,
for about the same amount, in at least MIN_MONTHS of the last LOOKBACK_MONTHS
months. It's recognised by the shop/payee (what a statement line says), else by
the product. Groceries don't qualify: several trips a month isn't "about once".
"""

from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast import exclusions
from app.models import LineItem, Receipt, ReceiptStatus
from app.rules import fold
from app.stats import effective_date, month_bounds, shift_month

# Stored alongside the shopping-forecast exclusions, with its own kind.
RECURRING = "recurring"

LOOKBACK_MONTHS = 6
MIN_MONTHS = 3
# Payments per month it appears in, on average: 1.4 lets an odd double month through.
MAX_PER_MONTH = 1.4
# Median distance of the day of month from the usual day.
MAX_DAY_SPREAD = 5
# Median relative distance of the amount from the usual amount.
MAX_AMOUNT_SPREAD = 0.25
# Days after the usual day before a missing payment counts as late.
GRACE_DAYS = 3
SOON_DAYS = 7


@dataclass
class Recurring:
    key: str
    name: str
    category: str
    day: int  # usual day of month
    amount: float  # usual amount
    months: int  # months seen in
    paid_this_month: float | None
    last_date: date

    status: str = ""  # paid, late, soon, later


def _entries(db: Session, start: date, end: date) -> list[tuple[str, str, str, float, date]]:
    """(key, display name, category, amount, day), one per receipt."""
    rows = db.execute(
        select(
            Receipt.id, Receipt.store_name, LineItem.product, LineItem.description,
            LineItem.category, LineItem.amount, effective_date,
        )
        .join(LineItem, LineItem.receipt_id == Receipt.id)
        .where(
            Receipt.status == ReceiptStatus.PROCESSED,
            effective_date >= start,
            effective_date < end,
            LineItem.amount > 0,
        )
    ).all()
    per_receipt: dict[int, list] = {}
    for rid, store, product, description, category, amount, day in rows:
        entry = per_receipt.setdefault(rid, [store, product or description, {}, 0.0, day])
        entry[2][category] = entry[2].get(category, 0) + amount
        entry[3] += amount
    out = []
    for store, label, categories, amount, day in per_receipt.values():
        name = (store or label or "").strip()
        if not name:
            continue
        category = max(categories, key=categories.get)
        out.append((fold(name), name, category, amount, day))
    return out


def detect(db: Session, today: date | None = None) -> list[Recurring]:
    today = today or date.today()
    first = shift_month(today.year, today.month, -LOOKBACK_MONTHS)
    start, _ = month_bounds(*first)
    _, end = month_bounds(today.year, today.month)
    month_start, _ = month_bounds(today.year, today.month)
    hidden = exclusions(db).get(RECURRING, set())

    groups: dict[str, list] = {}
    for key, name, category, amount, day in _entries(db, start, end):
        if key not in hidden:
            groups.setdefault(key, []).append((name, category, amount, day))

    found = []
    for key, entries in groups.items():
        past = [e for e in entries if e[3] < month_start]
        months = {(e[3].year, e[3].month) for e in past}
        if len(months) < MIN_MONTHS or len(past) / len(months) > MAX_PER_MONTH:
            continue
        usual_day = round(median(e[3].day for e in past))
        if median(abs(e[3].day - usual_day) for e in past) > MAX_DAY_SPREAD:
            continue
        usual_amount = median(e[2] for e in past)
        if usual_amount <= 0 or median(abs(e[2] / usual_amount - 1) for e in past) > MAX_AMOUNT_SPREAD:
            continue
        this_month = [e for e in entries if e[3] >= month_start]
        latest = max(entries, key=lambda e: e[3])
        item = Recurring(
            key=key,
            name=latest[0],
            category=latest[1],
            day=usual_day,
            amount=usual_amount,
            months=len(months),
            paid_this_month=sum(e[2] for e in this_month) if this_month else None,
            last_date=latest[3],
        )
        if this_month:
            item.status = "paid"
        elif today.day > usual_day + GRACE_DAYS:
            item.status = "late"
        elif usual_day - today.day <= SOON_DAYS:
            item.status = "soon"
        else:
            item.status = "later"
        found.append(item)

    rank = {"late": 0, "soon": 1, "later": 2, "paid": 3}
    return sorted(found, key=lambda r: (rank[r.status], r.day))


def reminders(items: list[Recurring]) -> list[Recurring]:
    """What the dashboard nags about: late, or due within a week."""
    return [r for r in items if r.status in ("late", "soon")]
