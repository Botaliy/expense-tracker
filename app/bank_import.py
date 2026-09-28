"""Bank statement import: card payments that never had a paper receipt.

Flow: a CSV is parsed into ``BankTransaction`` rows (status ``pending``), each
checked against the receipts already recorded; the user reviews the preview,
then confirms. Confirming links matched rows to their receipt and turns the
chosen new ones into expenses (``Receipt.source == "bank"``).

Double counting is avoided three ways:

* a statement row that matches an existing receipt (same amount to the cent,
  date within ``MATCH_DAYS``) is linked to it instead of creating anything;
* every row has a fingerprint, so importing the same (or an overlapping)
  statement again skips the rows already seen;
* a receipt photo uploaded *after* the import replaces the bank-made expense
  it matches (``absorb_bank_twin``): the photo has the line items.

Revolut's export has a fixed layout. Other banks (Bank of Cyprus, …) are read
by recognising column names; when that fails, Claude is shown the header and a
few rows and says which column is which.
"""

import csv
from collections import Counter
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categories import DEFAULT_CATEGORY
from app.models import BankTransaction, LineItem, Receipt, ReceiptStatus
from app.rules import find_rule, fold
from app.stats import effective_date

REVOLUT = "revolut"
GENERIC = "bank"

CARD, TRANSFER, FEE = "card", "transfer", "fee"

# Card payments post a day or two after the purchase.
MATCH_DAYS = 2


class StatementError(ValueError):
    pass


@dataclass
class StatementRow:
    booked_on: date
    amount: float  # spent, positive
    currency: str | None
    description: str
    kind: str


@dataclass
class ColumnMap:
    date: int
    description: int
    amount: int | None = None
    debit: int | None = None
    currency: int | None = None
    # For a single amount column: True when money spent is written negative.
    spent_negative: bool = True


# ---------- reading the file ----------

def decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1253", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StatementError("Не удалось прочитать файл как текст")


def sniff_delimiter(text: str) -> str:
    """The separator most lines agree on.

    csv.Sniffer is easily fooled by European files: "23,40" in a ';'-separated
    statement looks like a comma-separated one, and a few lines of account
    details before the table throw it off completely.
    """
    lines = [line for line in text.splitlines()[:50] if line.strip()]
    best, best_score = ",", -1
    for delimiter in (";", "\t", ",", "|"):
        counts = [line.count(delimiter) for line in lines]
        common = Counter(c for c in counts if c > 0).most_common(1)
        score = common[0][1] if common and common[0][0] >= 2 else 0
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text))
    rows = [[cell.strip() for cell in row] for row in reader]
    return [row for row in rows if any(row)]


def parse_number(value: str) -> float | None:
    """``-12.50``, ``1,234.56``, ``1.234,56``, ``€ 12,50``, ``12.50 DR`` → float."""
    text = value.strip()
    if not text:
        return None
    negative = text.startswith("-") or text.startswith("−") or text.endswith("-") or (
        text.startswith("(") and text.endswith(")")
    )
    if re.search(r"\b(DR|Dr|dr)\b", text):
        negative = True
    digits = re.sub(r"[^\d.,]", "", text)
    if not digits or not re.search(r"\d", digits):
        return None
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        head, _, tail = digits.rpartition(",")
        digits = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else digits.replace(",", "")
    try:
        number = float(digits)
    except ValueError:
        return None
    return -number if negative else number


DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y", "%d %b %Y", "%d %B %Y")


def parse_date(value: str) -> date | None:
    """European day-first dates, optionally followed by a time."""
    text = value.strip()
    if not text:
        return None
    candidates = [text, re.split(r"[ T]", text)[0]]
    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


# ---------- Revolut ----------

REVOLUT_HEADER = {"type", "started date", "description", "amount", "currency", "state"}
REVOLUT_KINDS = {"CARD_PAYMENT": CARD, "TRANSFER": TRANSFER, "FEE": FEE}
REVOLUT_DEAD_STATES = {"REVERTED", "DECLINED", "FAILED"}


def is_revolut(header: list[str]) -> bool:
    return REVOLUT_HEADER <= {h.strip().lower() for h in header}


def parse_revolut(rows: list[list[str]]) -> list[StatementRow]:
    header = [h.strip().lower() for h in rows[0]]
    col = {name: header.index(name) for name in header}
    out = []
    for row in rows[1:]:
        cell = lambda name: row[col[name]] if name in col and col[name] < len(row) else ""  # noqa: E731
        kind = REVOLUT_KINDS.get(cell("type").upper())
        if kind is None or cell("state").upper() in REVOLUT_DEAD_STATES:
            continue  # top-ups, exchanges, ATM cash, refunds
        amount = parse_number(cell("amount"))
        if amount is None or amount >= 0:
            continue  # money in
        fee = parse_number(cell("fee")) or 0.0
        day = parse_date(cell("started date"))
        if day is None:
            continue
        out.append(StatementRow(
            booked_on=day,
            amount=round(-amount + abs(fee), 2),
            currency=cell("currency") or None,
            description=cell("description") or "—",
            kind=kind,
        ))
    return out


# ---------- any other bank ----------

DATE_NAMES = ("transaction date", "posting date", "date", "ημερομηνία", "value date")
DESC_NAMES = ("description", "details", "narrative", "περιγραφή", "merchant", "payee", "reference", "αιτιολογία")
AMOUNT_NAMES = ("amount", "ποσό")
DEBIT_NAMES = ("debit", "χρέωση", "withdrawal", "money out", "paid out")
CURRENCY_NAMES = ("currency", "νόμισμα", "ccy")

TRANSFER_WORDS = ("transfer", "μεταφορά", "sepa", "standing order", "trf", "iban")
FEE_WORDS = ("fee", "charge", "commission", "προμήθεια", "χρέωση συντήρησης")
SKIP_WORDS = ("atm", "cash withdrawal", "ανάληψη")


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for name in names:  # earlier names win: "transaction date" before "value date"
        for i, h in enumerate(lowered):
            if h == name or h.startswith(name + " ") or h.endswith(" " + name) or name in h.split("/"):
                return i
    for name in names:
        for i, h in enumerate(lowered):
            if name in h:
                return i
    return None


def guess_columns(rows: list[list[str]]) -> tuple[int, ColumnMap] | None:
    """(header row index, columns), looking through the first rows for a header."""
    for index, row in enumerate(rows[:15]):
        date_col = _find(row, DATE_NAMES)
        desc_col = _find(row, DESC_NAMES)
        amount_col = _find(row, AMOUNT_NAMES)
        debit_col = _find(row, DEBIT_NAMES)
        if date_col is None or desc_col is None or (amount_col is None and debit_col is None):
            continue
        return index, ColumnMap(
            date=date_col,
            description=desc_col,
            amount=amount_col if debit_col is None else None,
            debit=debit_col,
            currency=_find(row, CURRENCY_NAMES),
        )
    return None


def kind_of(description: str) -> str | None:
    text = description.casefold()
    if any(word in text for word in SKIP_WORDS):
        return None
    if any(word in text for word in FEE_WORDS):
        return FEE
    if any(word in text for word in TRANSFER_WORDS):
        return TRANSFER
    return CARD


def parse_with_columns(rows: list[list[str]], columns: ColumnMap) -> list[StatementRow]:
    out = []
    for row in rows:
        def cell(i: int | None) -> str:
            return row[i] if i is not None and i < len(row) else ""

        day = parse_date(cell(columns.date))
        if day is None:
            continue  # header, totals, blank lines
        if columns.debit is not None:
            value = parse_number(cell(columns.debit))
            spent = abs(value) if value else None
        else:
            value = parse_number(cell(columns.amount))
            if value is None:
                spent = None
            elif columns.spent_negative:
                spent = -value if value < 0 else None
            else:
                spent = value if value > 0 else None
        if not spent:
            continue
        description = " ".join(cell(columns.description).split()) or "—"
        kind = kind_of(description)
        if kind is None:
            continue
        out.append(StatementRow(
            booked_on=day,
            amount=round(spent, 2),
            currency=cell(columns.currency) or None,
            description=description,
            kind=kind,
        ))
    return out


def parse_statement(content: bytes, map_columns=None) -> tuple[str, list[StatementRow]]:
    """(bank, rows spent). ``map_columns(rows) -> (header_index, ColumnMap) | None``
    is the fallback for layouts not recognised by name (Claude, in production)."""
    rows = read_rows(decode(content))
    if not rows:
        raise StatementError("Файл пустой")
    if is_revolut(rows[0]):
        return REVOLUT, parse_revolut(rows)
    guess = guess_columns(rows)
    if guess is None and map_columns is not None:
        guess = map_columns(rows)
    if guess is None:
        raise StatementError("Не нашёл в файле колонки с датой, описанием и суммой")
    header_index, columns = guess
    return GENERIC, parse_with_columns(rows[header_index + 1:], columns)


def fingerprints(bank: str, rows: list[StatementRow]) -> list[str]:
    """Stable per row. Identical rows (two coffees, same day, same price) are
    told apart by their order, which the same statement always repeats."""
    seen: dict[tuple, int] = {}
    out = []
    for row in rows:
        key = (bank, row.booked_on.isoformat(), round(row.amount * 100), (row.currency or "").upper(), fold(row.description))
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append(hashlib.sha256(repr((*key, n)).encode()).hexdigest())
    return out


# ---------- matching with receipts ----------

EURO = {"", "EUR", "EURO", "EUROS", "€"}


def _same_currency(a: str | None, b: str | None) -> bool:
    a, b = (a or "").strip().upper(), (b or "").strip().upper()
    return a == b or (a in EURO and b in EURO)


def _claimed_receipts(db: Session) -> set[int]:
    return set(db.scalars(
        select(BankTransaction.receipt_id).where(BankTransaction.receipt_id.is_not(None))
    )) | set(db.scalars(
        select(BankTransaction.match_receipt_id).where(
            BankTransaction.status == "pending", BankTransaction.match_receipt_id.is_not(None)
        )
    ))


def find_match(db: Session, txn: BankTransaction, taken: set[int]) -> Receipt | None:
    """The recorded receipt this payment most likely is: same amount, close date."""
    rows = db.execute(
        select(Receipt, effective_date).where(
            Receipt.status == ReceiptStatus.PROCESSED,
            Receipt.source.is_(None),
            effective_date >= txn.booked_on - timedelta(days=MATCH_DAYS),
            effective_date <= txn.booked_on + timedelta(days=MATCH_DAYS),
        )
    ).all()
    candidates = [
        (receipt, day) for receipt, day in rows
        if receipt.id not in taken
        and receipt.total_amount is not None
        and abs(receipt.total_amount - txn.amount) < 0.005
        and _same_currency(receipt.currency, txn.currency)
    ]
    if not candidates:
        return None
    description = fold(txn.description)

    def score(candidate):
        receipt, day = candidate
        store = fold(receipt.store_name)
        name_hit = bool(store) and (store in description or description.split(" ")[0] in store)
        return (not name_hit, abs((day - txn.booked_on).days), receipt.id)

    return min(candidates, key=score)[0]


@dataclass
class ImportResult:
    bank: str
    added: int
    already_seen: int
    matched: int


def stage(db: Session, bank: str, rows: list[StatementRow]) -> ImportResult:
    """Put new statement rows into the preview, each with its suggested match."""
    known = set(db.scalars(select(BankTransaction.fingerprint)))
    taken = _claimed_receipts(db)
    added = seen = matched = 0
    for row, fingerprint in zip(rows, fingerprints(bank, rows)):
        if fingerprint in known:
            seen += 1
            continue
        known.add(fingerprint)
        txn = BankTransaction(
            bank=bank, fingerprint=fingerprint, booked_on=row.booked_on, amount=row.amount,
            currency=row.currency, description=row.description[:255], kind=row.kind, status="pending",
        )
        match = find_match(db, txn, taken)
        if match is not None:
            txn.match_receipt_id = match.id
            taken.add(match.id)
            matched += 1
        db.add(txn)
        added += 1
    db.commit()
    return ImportResult(bank=bank, added=added, already_seen=seen, matched=matched)


def pending(db: Session) -> list[BankTransaction]:
    return list(db.scalars(
        select(BankTransaction)
        .where(BankTransaction.status == "pending")
        .order_by(BankTransaction.booked_on.desc(), BankTransaction.id)
    ))


def default_included(txn: BankTransaction) -> bool:
    """Purchases and fees are expenses; a transfer may be rent or money to yourself."""
    return txn.kind != TRANSFER


@dataclass
class Classified:
    category: str
    product: str | None
    store: str | None


def confirm(
    db: Session,
    include: set[int],
    keep_match: set[int],
    classify=None,
) -> dict:
    """Apply the reviewed preview.

    ``include``: ids to record as expenses; ``keep_match``: matched ids whose
    match the user confirmed (the rest of the matched ones, if also in
    ``include``, become expenses of their own). ``classify(descriptions) ->
    list[Classified | None]`` fills category, product and a clean shop name.
    """
    rows = pending(db)
    to_create: list[BankTransaction] = []
    linked = skipped = 0
    for txn in rows:
        if txn.match_receipt_id and txn.id in keep_match and db.get(Receipt, txn.match_receipt_id):
            txn.status = "matched"
            txn.receipt_id = txn.match_receipt_id
            linked += 1
        elif txn.id in include:
            to_create.append(txn)
        else:
            txn.status = "skipped"
            skipped += 1

    # Learned rules first; the model only sees what no rule covers.
    answers: dict[int, Classified] = {}
    unknown = []
    for txn in to_create:
        rule = find_rule(db, txn.description, None, any_store=True)
        if rule:
            rule.hits += 1
            answers[txn.id] = Classified(rule.category, rule.product, rule.store_name)
        else:
            unknown.append(txn)
    if unknown and classify is not None:
        for txn, result in zip(unknown, classify([t.description for t in unknown])):
            if result is not None:
                answers[txn.id] = result

    for txn in to_create:
        guess = answers.get(txn.id) or Classified(DEFAULT_CATEGORY, None, None)
        receipt = Receipt(
            image_path=None,
            status=ReceiptStatus.PROCESSED,
            source="bank",
            store_name=guess.store or txn.description,
            purchase_date=txn.booked_on,
            total_amount=txn.amount,
            currency=txn.currency,
        )
        receipt.items.append(LineItem(
            description=txn.description, amount=txn.amount, category=guess.category, product=guess.product,
        ))
        db.add(receipt)
        db.flush()
        txn.status = "created"
        txn.receipt_id = receipt.id
    db.commit()
    return {"created": len(to_create), "linked": linked, "skipped": skipped}


def discard_pending(db: Session) -> None:
    """Drop the preview; the same file can be imported again later."""
    for txn in pending(db):
        db.delete(txn)
    db.commit()


def absorb_bank_twin(db: Session, receipt: Receipt) -> Receipt | None:
    """A photo of a purchase already imported from the bank replaces that entry.

    Called once a photo is recognized. Only an unambiguous twin (exactly one
    bank-made expense with the same amount within MATCH_DAYS) is replaced.
    Returns the removed twin, if any. Caller commits.
    """
    if receipt.source == "bank" or not receipt.total_amount:
        return None
    day = receipt.purchase_date or receipt.uploaded_at.date()
    twins = [
        other for other in db.scalars(
            select(Receipt).where(
                Receipt.source == "bank",
                Receipt.id != receipt.id,
                Receipt.purchase_date >= day - timedelta(days=MATCH_DAYS),
                Receipt.purchase_date <= day + timedelta(days=MATCH_DAYS),
            )
        )
        if other.total_amount is not None
        and abs(other.total_amount - receipt.total_amount) < 0.005
        and _same_currency(other.currency, receipt.currency)
    ]
    if len(twins) != 1:
        return None
    twin = twins[0]
    for txn in db.scalars(select(BankTransaction).where(BankTransaction.receipt_id == twin.id)):
        txn.receipt_id = receipt.id
        txn.status = "matched"
    db.delete(twin)
    return twin
