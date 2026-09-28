"""What the Claude API costs: every request is logged with its tokens and price.

Prices are Anthropic's list prices in USD per million tokens. They're an
estimate of the bill, not the bill itself: the Anthropic console is the source
of truth (taxes, credits and price changes aren't reflected here).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import database
from app.models import ApiCall
from app.stats import MONTHS_NOMINATIVE, MONTHS_SHORT

logger = logging.getLogger(__name__)

# Purposes, as stored in api_calls.purpose and shown on the usage page.
RECEIPT = "receipt"
CLASSIFY = "classify"
LABEL = "label"

PURPOSE_LABELS = {
    RECEIPT: "Распознавание чеков",
    CLASSIFY: "Подсказка при ручном вводе",
    LABEL: "Разметка товаров",
}
# For the narrow "latest calls" table.
PURPOSE_SHORT = {RECEIPT: "Чек", CLASSIFY: "Ручной ввод", LABEL: "Разметка"}


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float

    @property
    def cache_write(self) -> float:  # 5-minute cache writes cost 1.25× input
        return self.input * 1.25

    @property
    def cache_read(self) -> float:  # cache hits cost 0.1× input
        return self.input * 0.1


PRICES = {
    "claude-haiku-4-5": Price(input=1.00, output=5.00),
    "claude-sonnet-5": Price(input=2.00, output=10.00),
    "claude-sonnet-4-6": Price(input=3.00, output=15.00),
    "claude-opus-5": Price(input=5.00, output=25.00),
}


def price_for(model: str) -> Price | None:
    """Responses may name a dated snapshot ("claude-haiku-4-5-20251001"): match by prefix."""
    matches = [key for key in PRICES if model == key or model.startswith(key + "-")]
    return PRICES[max(matches, key=len)] if matches else None


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_write_tokens * price.cache_write
        + cache_read_tokens * price.cache_read
    ) / 1_000_000


def record_call(purpose: str, response, receipt_id: int | None = None) -> None:
    """Log one API response's usage. Never raises: logging mustn't break the request."""
    try:
        usage = response.usage
        model = response.model
        tokens = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        }
        with database.SessionLocal() as db:
            db.add(ApiCall(
                purpose=purpose,
                model=model,
                cost_usd=cost_usd(model, **tokens),
                receipt_id=receipt_id,
                **tokens,
            ))
            db.commit()
    except Exception:
        logger.exception("Could not record API usage for %s", purpose)


# ---------- reports for the usage page ----------

def _totals(db: Session, *conditions) -> dict:
    calls, cost, tokens_in, tokens_out = db.execute(
        select(
            func.count(ApiCall.id),
            func.coalesce(func.sum(ApiCall.cost_usd), 0.0),
            func.coalesce(func.sum(ApiCall.input_tokens + ApiCall.cache_write_tokens + ApiCall.cache_read_tokens), 0),
            func.coalesce(func.sum(ApiCall.output_tokens), 0),
        ).where(*conditions)
    ).one()
    return {"calls": calls, "cost": cost, "input_tokens": tokens_in, "output_tokens": tokens_out}


def current_month_usage(db: Session) -> dict:
    """Calls and cost so far this month, for the dashboard's link to the usage page."""
    today = date.today()
    start = datetime(today.year, today.month, 1)
    return _totals(db, ApiCall.created_at >= start)


def usage_report(
    db: Session, month_start: date, month_end: date, months: list[tuple[int, int]], recent: int = 50
) -> dict:
    """This month, all time, per purpose, per month, and the latest calls."""
    in_month = (ApiCall.created_at >= month_start, ApiCall.created_at < month_end)

    by_purpose = []
    for purpose, label in PURPOSE_LABELS.items():
        month = _totals(db, ApiCall.purpose == purpose, *in_month)
        overall = _totals(db, ApiCall.purpose == purpose)
        if overall["calls"]:
            by_purpose.append({
                "label": label,
                "month": month,
                "overall": overall,
                "average": overall["cost"] / overall["calls"],
            })

    month_key = func.strftime("%Y-%m", ApiCall.created_at)
    per_month = dict(db.execute(
        select(month_key, func.coalesce(func.sum(ApiCall.cost_usd), 0.0)).group_by(month_key)
    ).all())
    chart = [
        {
            "key": f"{y:04d}-{m:02d}",
            "short": MONTHS_SHORT[m - 1],
            "name": f"{MONTHS_NOMINATIVE[m - 1]} {y}",
            "total": per_month.get(f"{y:04d}-{m:02d}", 0.0),
        }
        for y, m in months
    ]

    latest = db.scalars(
        select(ApiCall).order_by(ApiCall.created_at.desc(), ApiCall.id.desc()).limit(recent)
    ).all()
    unpriced = db.scalar(select(func.count(ApiCall.id)).where(ApiCall.cost_usd.is_(None)))

    return {
        "month": _totals(db, *in_month),
        "overall": _totals(db),
        "by_purpose": by_purpose,
        "chart": chart,
        "latest": latest,
        "unpriced": unpriced,
    }
