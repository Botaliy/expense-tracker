"""Corrections that stick: fix an item's category once, and the same line is
filed that way from then on.

A rule is keyed by the item's text (case- and spacing-insensitive) and the
store it was bought in, so "ΓΑΛΑ 1L" at Alphamega learns its own answer. A
rule learned without a store (hand-entered expenses) applies anywhere. Rules
win over the model: they are what the user said, the model only guesses.
They also save a model call when a hand-entered expense matches one.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CategoryRule, LineItem, Receipt


# Lines the app adds itself (app.receipts._total_adjustment): their text says
# nothing about what was bought, so a correction on one is a one-off.
GENERIC_LINES = {"скидка", "корректировка"}


def fold(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


@dataclass
class RuleMatch:
    category: str
    product: str | None


def find_rule(db: Session, description: str, store_name: str | None) -> CategoryRule | None:
    """This store's rule for the text, else one learned without a store."""
    key = fold(description)
    if not key:
        return None
    rules = {
        r.store_key: r
        for r in db.scalars(select(CategoryRule).where(CategoryRule.key == key))
    }
    return rules.get(fold(store_name)) or rules.get("")


def learn(db: Session, item: LineItem, store_name: str | None) -> None:
    """Remember the item's current category and product for its text and store.

    Caller commits.
    """
    key = fold(item.description)
    if not key or key in GENERIC_LINES:
        return
    store_key = fold(store_name)
    rule = db.scalar(
        select(CategoryRule).where(CategoryRule.key == key, CategoryRule.store_key == store_key)
    )
    if rule is None:
        rule = CategoryRule(key=key, store_key=store_key, hits=0)
        db.add(rule)
    rule.description = item.description
    rule.store_name = store_name
    rule.category = item.category
    rule.product = item.product


def apply_rules(db: Session, receipt: Receipt) -> int:
    """Override the model's guesses on a freshly recognized receipt. Returns how many applied."""
    applied = 0
    for item in receipt.items:
        rule = find_rule(db, item.description, receipt.store_name)
        if rule is None:
            continue
        item.category = rule.category
        if rule.product:
            item.product = rule.product
        rule.hits += 1
        applied += 1
    return applied


def match(db: Session, description: str, store_name: str | None = None) -> RuleMatch | None:
    """Category and product a rule dictates for a hand-entered expense, if any."""
    rule = find_rule(db, description, store_name)
    if rule is None:
        return None
    rule.hits += 1
    db.commit()
    return RuleMatch(category=rule.category, product=rule.product)


def all_rules(db: Session) -> list[CategoryRule]:
    return list(db.scalars(select(CategoryRule).order_by(CategoryRule.updated_at.desc())))


def delete_rule(db: Session, rule_id: int) -> None:
    rule = db.get(CategoryRule, rule_id)
    if rule is not None:
        db.delete(rule)
        db.commit()
