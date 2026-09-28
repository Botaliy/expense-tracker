"""The shopping list: what the forecast says will run out, plus lines typed by hand.

A forecast line is ticked off for the due date it was predicted for. The next
receipt with that product moves the prediction to a new due date, so the line
comes back on its own when it's needed again. Hand-typed lines stay until
ticked; ticked ones linger for a day (so a mis-tap can be undone) and then go.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.forecast import Prediction, predict, upcoming
from app.models import ShoppingItem

KEEP_DONE = timedelta(days=1)


@dataclass
class ListLine:
    name: str
    done: bool
    prediction: Prediction | None = None
    item_id: int | None = None


def shopping_list(db: Session, today: date | None = None) -> list[ListLine]:
    now = datetime.now(UTC).replace(tzinfo=None)
    db.execute(
        delete(ShoppingItem).where(ShoppingItem.done.is_(True), ShoppingItem.done_at < now - KEEP_DONE)
    )
    db.commit()

    rows = list(db.scalars(select(ShoppingItem).order_by(ShoppingItem.created_at)))
    ticked = {(r.product, r.due_date) for r in rows if r.product and r.done}
    lines = [
        ListLine(name=p.product, done=(p.product, p.due_date) in ticked, prediction=p)
        for p in upcoming(predict(db, today))
    ]
    lines += [ListLine(name=r.name, done=r.done, item_id=r.id) for r in rows if not r.product]
    # Still to buy first; within that, forecast order then hand-typed order.
    return sorted(lines, key=lambda line: line.done)


def tick_forecast(db: Session, product: str, due_date: date, done: bool) -> None:
    existing = db.scalar(
        select(ShoppingItem).where(ShoppingItem.product == product, ShoppingItem.due_date == due_date)
    )
    if done and existing is None:
        db.add(ShoppingItem(
            name=product, product=product, due_date=due_date, done=True,
            done_at=datetime.now(UTC).replace(tzinfo=None),
        ))
    elif not done and existing is not None:
        db.delete(existing)
    db.commit()


def add_item(db: Session, name: str) -> None:
    name = " ".join(name.split())[:128]
    if name:
        db.add(ShoppingItem(name=name, done=False))
        db.commit()


def toggle_item(db: Session, item_id: int, done: bool) -> None:
    item = db.get(ShoppingItem, item_id)
    if item is not None and item.product is None:
        item.done = done
        item.done_at = datetime.now(UTC).replace(tzinfo=None) if done else None
        db.commit()


def clear_done(db: Session) -> None:
    db.execute(delete(ShoppingItem).where(ShoppingItem.product.is_(None), ShoppingItem.done.is_(True)))
    db.commit()
