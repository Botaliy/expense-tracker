"""Shopping forecast: what's likely to run out soon, from how often it's bought.

Each product's purchase days give the usual gap between purchases; the next
purchase is expected one median gap after the last. No model is involved —
with a few dozen receipts, a median is steadier than anything fancier.

Some regular purchases aren't stock that runs out (a café, parking, the bag at
the till), so products and whole categories can be excluded; the list is kept
in the database and edited from the forecast page.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ForecastExclusion, LineItem
from app.stats import effective_date

PRODUCT = "product"
CATEGORY = "category"

# Seeded once, when the exclusions table is first created; editable after that.
DEFAULT_EXCLUDED_CATEGORIES = [
    "Кафе/рестораны",
    "Транспорт",
    "Авто",
    "Жильё/коммуналка",
    "Связь/интернет",
    "Развлечения",
]
DEFAULT_EXCLUDED_PRODUCTS = ["plastic bag"]

# Purchases this many days apart or closer count as one shopping trip:
# pineapple on the 25th and 26th says nothing about a one-day need.
MERGE_GAP_DAYS = 1
# Three trips give two gaps — the least a median means anything with.
MIN_PURCHASES = 3
# Median absolute deviation of the gaps, relative to the median gap. Above
# this the product is bought too irregularly to predict (beer every 1–9 days).
MAX_SPREAD = 0.5
# Not bought for this many usual gaps: probably stopped buying it, or its
# receipts weren't uploaded. Either way, nagging about it isn't useful.
STALE_AFTER_GAPS = 2
# How far ahead the dashboard looks.
SOON_DAYS = 7


@dataclass
class Prediction:
    product: str
    category: str
    purchases: int
    interval_days: float
    last_date: date
    due_date: date
    days_until: int
    spread: float

    @property
    def status(self) -> str:
        """``due`` (today or overdue), ``soon`` (within SOON_DAYS) or ``later``."""
        if self.days_until <= 0:
            return "due"
        if self.days_until <= SOON_DAYS:
            return "soon"
        return "later"


def merge_trips(days: list[date]) -> list[date]:
    """Sorted distinct days, runs of close days collapsed to the latest one."""
    trips: list[date] = []
    for day in sorted(set(days)):
        if trips and (day - trips[-1]).days <= MERGE_GAP_DAYS:
            trips[-1] = day
        else:
            trips.append(day)
    return trips


def predict_product(product: str, category: str, days: list[date], today: date) -> Prediction | None:
    """Next purchase of one product, or None if its history doesn't support one."""
    trips = merge_trips(days)
    if len(trips) < MIN_PURCHASES:
        return None
    gaps = [(b - a).days for a, b in zip(trips, trips[1:])]
    interval = median(gaps)
    spread = median(abs(g - interval) for g in gaps) / interval
    if spread > MAX_SPREAD:
        return None
    last = trips[-1]
    if (today - last).days > STALE_AFTER_GAPS * interval:
        return None
    due = last + timedelta(days=round(interval))
    return Prediction(
        product=product,
        category=category,
        purchases=len(trips),
        interval_days=interval,
        last_date=last,
        due_date=due,
        days_until=(due - today).days,
        spread=spread,
    )


def exclusions(db: Session) -> dict[str, set[str]]:
    rows = db.execute(select(ForecastExclusion.kind, ForecastExclusion.value)).all()
    result: dict[str, set[str]] = {PRODUCT: set(), CATEGORY: set()}
    for kind, value in rows:
        result.setdefault(kind, set()).add(value)
    return result


def predict(db: Session, today: date | None = None) -> list[Prediction]:
    """Every predictable product, soonest first."""
    today = today or date.today()
    excluded = exclusions(db)
    rows = db.execute(
        select(LineItem.product, LineItem.category, effective_date)
        .join(LineItem.receipt)
        .where(LineItem.product.is_not(None), LineItem.amount > 0, effective_date <= today)
    ).all()

    days: dict[str, list[date]] = {}
    categories: dict[str, Counter] = {}
    for product, category, day in rows:
        if product in excluded[PRODUCT]:
            continue
        days.setdefault(product, []).append(day)
        categories.setdefault(product, Counter())[category] += 1

    predictions = []
    for product, product_days in days.items():
        # The model occasionally files the same product differently; go with
        # the usual one so a stray label doesn't dodge (or trip) a category filter.
        category = categories[product].most_common(1)[0][0]
        if category in excluded[CATEGORY]:
            continue
        prediction = predict_product(product, category, product_days, today)
        if prediction:
            predictions.append(prediction)
    return sorted(predictions, key=lambda p: (p.days_until, p.product))


def upcoming(predictions: list[Prediction]) -> list[Prediction]:
    """What the dashboard shows: due now or within SOON_DAYS."""
    return [p for p in predictions if p.status != "later"]


def add_exclusion(db: Session, kind: str, value: str) -> None:
    exists = db.scalar(
        select(ForecastExclusion.id).where(ForecastExclusion.kind == kind, ForecastExclusion.value == value)
    )
    if not exists:
        db.add(ForecastExclusion(kind=kind, value=value))
        db.commit()


def remove_exclusion(db: Session, kind: str, value: str) -> None:
    db.execute(
        delete(ForecastExclusion).where(ForecastExclusion.kind == kind, ForecastExclusion.value == value)
    )
    db.commit()


def set_excluded_categories(db: Session, categories: set[str]) -> None:
    db.execute(delete(ForecastExclusion).where(ForecastExclusion.kind == CATEGORY))
    db.add_all(ForecastExclusion(kind=CATEGORY, value=c) for c in sorted(categories))
    db.commit()


def seed_default_exclusions(db: Session) -> None:
    db.add_all(ForecastExclusion(kind=CATEGORY, value=c) for c in DEFAULT_EXCLUDED_CATEGORIES)
    db.add_all(ForecastExclusion(kind=PRODUCT, value=p) for p in DEFAULT_EXCLUDED_PRODUCTS)
    db.commit()
