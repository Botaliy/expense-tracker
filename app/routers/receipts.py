from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_client import ExpenseCategorizationError, categorize_expense
from app.auth import get_current_user
from app.categories import CATEGORIES, DEFAULT_CATEGORY
from app.database import get_db
from app.models import LineItem, Receipt
from app.receipts import create_manual_expense, create_receipt, process_receipt, save_upload
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_class=HTMLResponse)
def list_receipts(request: Request, db: Session = Depends(get_db)):
    receipts = db.execute(
        select(Receipt).order_by(Receipt.uploaded_at.desc())
    ).scalars().all()

    categories_by_receipt: dict[int, list[str]] = {}
    if receipts:
        cat_rows = db.execute(
            select(LineItem.receipt_id, LineItem.category)
            .where(LineItem.receipt_id.in_([r.id for r in receipts]))
            .distinct()
            .order_by(LineItem.category)
        ).all()
        for receipt_id, category in cat_rows:
            categories_by_receipt.setdefault(receipt_id, []).append(category)

    return templates.TemplateResponse(
        request,
        "receipts_list.html",
        {
            "receipts": receipts,
            "categories": CATEGORIES,
            "categories_by_receipt": categories_by_receipt,
            "today": date.today().isoformat(),
        },
    )


@router.post("/receipts", response_class=HTMLResponse)
async def upload_receipt(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    image_path = save_upload(file.filename or "receipt.jpg", content)
    receipt = create_receipt(db, image_path)
    process_receipt(db, receipt)

    return RedirectResponse(url=f"/receipts/{receipt.id}", status_code=303)


@router.post("/expenses/categorize", response_class=JSONResponse)
def categorize_manual_expense(
    description: str = Form(...),
    amount: float | None = Form(default=None),
):
    """Suggest a category for a hand-entered expense (used by the form's button)."""
    try:
        category = categorize_expense(description, amount)
    except ExpenseCategorizationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return {"category": category}


@router.post("/expenses", response_class=HTMLResponse)
def add_manual_expense(
    description: str = Form(...),
    amount: float = Form(...),
    category: str | None = Form(default=None),
    purchase_date: str | None = Form(default=None),
    store_name: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    parsed_date: date | None = None
    if purchase_date:
        try:
            parsed_date = datetime.fromisoformat(purchase_date).date()
        except ValueError:
            parsed_date = None

    # No category picked → let the model classify it from the description/amount.
    if not category or not category.strip():
        try:
            category = categorize_expense(description, amount)
        except ExpenseCategorizationError:
            category = DEFAULT_CATEGORY

    receipt = create_manual_expense(
        db,
        description=description,
        amount=amount,
        category=category,
        purchase_date=parsed_date,
        store_name=store_name,
    )
    return RedirectResponse(url=f"/receipts/{receipt.id}", status_code=303)


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_detail(request: Request, receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return templates.TemplateResponse(
        request,
        "receipt_detail.html",
        {"receipt": receipt, "categories": CATEGORIES},
    )


@router.post("/receipts/{receipt_id}", response_class=HTMLResponse)
def update_receipt(
    receipt_id: int,
    store_name: str | None = Form(default=None),
    purchase_date: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")

    receipt.store_name = (store_name or "").strip() or None
    if purchase_date is not None:
        stripped = purchase_date.strip()
        if not stripped:
            receipt.purchase_date = None
        else:
            try:
                receipt.purchase_date = datetime.fromisoformat(stripped).date()
            except ValueError:
                pass  # keep the existing date on unparseable input

    db.commit()
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/retry", response_class=HTMLResponse)
def retry_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    process_receipt(db, receipt)
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/items/{item_id}", response_class=HTMLResponse)
def update_item(
    receipt_id: int,
    item_id: int,
    category: str = Form(...),
    description: str | None = Form(default=None),
    amount: float | None = Form(default=None),
    quantity: float | None = Form(default=None),
    db: Session = Depends(get_db),
):
    item = db.get(LineItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")

    item.category = category
    if description is not None and description.strip():
        item.description = description.strip()
    if amount is not None and amount >= 0:
        item.amount = amount
    if quantity is not None:
        item.quantity = quantity if quantity > 0 else None

    # Keep the receipt total in sync with its line items.
    item.receipt.total_amount = sum(i.amount for i in item.receipt.items)

    db.commit()
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/category", response_class=HTMLResponse)
def update_all_items_category(
    receipt_id: int,
    category: str = Form(...),
    db: Session = Depends(get_db),
):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
    for item in receipt.items:
        item.category = category
    db.commit()
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/delete")
def delete_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    db.delete(receipt)
    db.commit()
    return RedirectResponse(url="/", status_code=303)
