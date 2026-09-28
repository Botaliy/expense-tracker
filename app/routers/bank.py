from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import bank_import
from app.ai_client import ExpenseCategorizationError, classify_statement, map_statement_columns
from app.auth import get_current_user
from app.database import get_db
from app.models import BankTransaction, Receipt
from app.products import known_products, known_stores
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])

BANK_NAMES = {bank_import.REVOLUT: "Revolut", bank_import.GENERIC: "банк"}


def _ai_columns(rows: list[list[str]]):
    answer = map_statement_columns(rows)
    if not answer or answer.get("date") is None or answer.get("description") is None:
        return None
    if answer.get("amount") is None and answer.get("debit") is None:
        return None
    return answer.get("header_row", 0), bank_import.ColumnMap(
        date=answer["date"],
        description=answer["description"],
        amount=answer.get("amount") if answer.get("debit") is None else None,
        debit=answer.get("debit"),
        currency=answer.get("currency"),
        spent_negative=answer.get("spent_negative", True),
    )


@router.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request,
    added: int | None = None,
    seen: int = 0,
    matched: int = 0,
    created: int | None = None,
    linked: int = 0,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    rows = bank_import.pending(db)
    matches = {
        r.id: r for r in db.scalars(
            select(Receipt).where(Receipt.id.in_([t.match_receipt_id for t in rows if t.match_receipt_id]))
        )
    }
    history = db.execute(
        select(BankTransaction.status, func.count()).group_by(BankTransaction.status)
    ).all()
    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "rows": rows,
            "matched_rows": [t for t in rows if t.match_receipt_id in matches],
            "new_rows": [t for t in rows if t.match_receipt_id not in matches],
            "matches": matches,
            "default_included": bank_import.default_included,
            "history": dict(history),
            "added": added,
            "seen": seen,
            "matched": matched,
            "created": created,
            "linked": linked,
            "error": error,
        },
    )


@router.post("/import")
def upload_statement(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = file.file.read()
    try:
        bank, rows = bank_import.parse_statement(content, map_columns=_ai_columns)
    except bank_import.StatementError as exc:
        return RedirectResponse(url=f"/import?error={quote(str(exc))}", status_code=303)
    if not rows:
        return RedirectResponse(url="/import?error=" + quote("В файле не нашлось списаний"), status_code=303)
    result = bank_import.stage(db, bank, rows)
    return RedirectResponse(
        url=f"/import?added={result.added}&seen={result.already_seen}&matched={result.matched}",
        status_code=303,
    )


@router.post("/import/confirm")
def confirm_import(
    include: list[int] = Form(default=[]),
    keep: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    products, stores = known_products(db), known_stores(db)

    def classify(descriptions: list[str]):
        try:
            return classify_statement(descriptions, products, stores)
        except ExpenseCategorizationError:
            return [None] * len(descriptions)

    result = bank_import.confirm(db, set(include), set(keep), classify)
    return RedirectResponse(
        url=f"/import?created={result['created']}&linked={result['linked']}", status_code=303
    )


@router.post("/import/discard")
def discard_import(db: Session = Depends(get_db)):
    bank_import.discard_pending(db)
    return RedirectResponse(url="/import", status_code=303)
