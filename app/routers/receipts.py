from datetime import date, datetime, timedelta

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.ai_client import (
    ExpenseCategorizationError,
    ExpenseClassification,
    classify_expense,
)
from app.auth import get_current_user
from app.categories import CATEGORIES, DEFAULT_CATEGORY
from app.database import get_db
from app import rules
from app.feed import month_feed
from app.models import LineItem, Receipt, ReceiptStatus
from app.products import known_products, normalize_product
from app.receipts import (
    create_manual_expense,
    create_receipt,
    process_receipt_in_background,
    save_upload,
)
from app.stats import month_bounds, month_nav, month_summary, parse_month
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/", response_class=HTMLResponse)
def list_receipts(
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM"),
    db: Session = Depends(get_db),
):
    year, mon = parse_month(month)
    start, end = month_bounds(year, mon)
    today = date.today()
    days = month_feed(db, start, end)

    return templates.TemplateResponse(
        request,
        "receipts_list.html",
        {
            "days": days,
            "summary": month_summary(db, year, mon),
            "has_pending": any(
                e.receipt.status == ReceiptStatus.PENDING for d in days for e in d.entries
            ),
            "nav": month_nav(year, mon),
            "categories": CATEGORIES,
            "quick_days": [today - timedelta(days=n) for n in range(3)],
        },
    )


def _parse_amount(value: str) -> float:
    """Accept what people type on a Russian keyboard: "1 890", "199,90"."""
    try:
        cleaned = value.replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("−", "-")
        return float(cleaned)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid amount")


def _sync_total(receipt: Receipt) -> None:
    receipt.total_amount = round(sum(i.amount for i in receipt.items), 2)


def _get_receipt(db: Session, receipt_id: int) -> Receipt:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


def _receipt_day(receipt: Receipt) -> date:
    return receipt.purchase_date or receipt.uploaded_at.date()


def _classify(db: Session, description: str, amount: float | None) -> ExpenseClassification | None:
    """Category + product from a learned rule or the model, or None when neither has one."""
    learned = rules.match(db, description)
    if learned:
        return ExpenseClassification(category=learned.category, product=learned.product)
    try:
        return classify_expense(description, amount, known_products(db))
    except ExpenseCategorizationError:
        return None


def _home_url(day: date | None, receipt_id: int) -> str:
    """Feed page for the month the receipt landed in, scrolled to its row."""
    day = day or date.today()
    return f"/?month={day:%Y-%m}#r-{receipt_id}"


# Plain ``def`` so FastAPI runs it in a threadpool; recognition itself happens
# in a background task, and the feed polls until it finishes.
@router.post("/receipts", response_class=HTMLResponse)
def upload_receipt(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    image_path = save_upload(file.filename or "receipt.jpg", content)
    receipt = create_receipt(db, image_path)
    background_tasks.add_task(process_receipt_in_background, receipt.id)

    return RedirectResponse(url=_home_url(None, receipt.id), status_code=303)


@router.post("/expenses/categorize", response_class=JSONResponse)
def categorize_manual_expense(
    description: str = Form(...),
    amount: float | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Suggest a category and product while the manual-entry form is filled in."""
    learned = rules.match(db, description)
    if learned:
        return {"category": learned.category, "product": learned.product, "source": "rule"}
    try:
        result = classify_expense(description, amount, known_products(db))
    except ExpenseCategorizationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return {"category": result.category, "product": result.product}


@router.post("/expenses", response_class=HTMLResponse)
def add_manual_expense(
    description: str = Form(...),
    amount: str = Form(...),
    category: str | None = Form(default=None),
    purchase_date: str | None = Form(default=None),
    store_name: str | None = Form(default=None),
    product: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    parsed_amount = _parse_amount(amount)
    product = normalize_product(product)

    parsed_date: date | None = None
    if purchase_date:
        try:
            parsed_date = datetime.fromisoformat(purchase_date).date()
        except ValueError:
            parsed_date = None

    # Whatever the form didn't already bring (the sheet pre-fetches a
    # suggestion while typing), one model call fills in.
    if not category or not category.strip() or product is None:
        result = _classify(db, description, parsed_amount)
        if not category or not category.strip():
            category = result.category if result else DEFAULT_CATEGORY
        if product is None and result:
            product = result.product

    receipt = create_manual_expense(
        db,
        description=description,
        amount=parsed_amount,
        category=category,
        purchase_date=parsed_date,
        store_name=store_name,
        product=product,
    )
    return RedirectResponse(url=_home_url(parsed_date, receipt.id), status_code=303)


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_detail(request: Request, receipt_id: int, db: Session = Depends(get_db)):
    receipt = _get_receipt(db, receipt_id)

    by_category: dict[str, float] = {}
    for item in receipt.items:
        by_category[item.category] = by_category.get(item.category, 0) + item.amount

    return templates.TemplateResponse(
        request,
        "receipt_detail.html",
        {
            "receipt": receipt,
            "day": _receipt_day(receipt),
            "by_category": sorted(by_category.items(), key=lambda c: -c[1]),
            "back_url": _home_url(_receipt_day(receipt), receipt.id),
            "known_products": known_products(db),
            "categories": CATEGORIES,
        },
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
def retry_receipt(
    receipt_id: int,
    background_tasks: BackgroundTasks,
    next_url: str | None = Form(default=None, alias="next"),
    db: Session = Depends(get_db),
):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if receipt.image_path is None:
        raise HTTPException(status_code=400, detail="Manual expense has no photo to recognize")
    receipt.status = ReceiptStatus.PENDING
    receipt.error_message = None
    db.commit()
    background_tasks.add_task(process_receipt_in_background, receipt_id)
    # Only local paths: never bounce to another site.
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = f"/receipts/{receipt_id}"
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/receipts/{receipt_id}/items/{item_id}", response_class=HTMLResponse)
def update_item(
    receipt_id: int,
    item_id: int,
    category: str = Form(...),
    description: str | None = Form(default=None),
    amount: str | None = Form(default=None),
    quantity: str | None = Form(default=None),
    product: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    item = db.get(LineItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")

    before = (item.category, item.product)
    item.category = category
    if description is not None and description.strip():
        item.description = description.strip()
    if amount is not None and amount.strip():  # negative is allowed: discount lines
        item.amount = _parse_amount(amount)
    if quantity is not None:
        parsed_quantity = _parse_amount(quantity) if quantity.strip() else 0
        item.quantity = parsed_quantity if parsed_quantity > 0 else None
    if product is not None:
        item.product = normalize_product(product)

    # A changed category or product is a correction worth remembering.
    if (item.category, item.product) != before:
        rules.learn(db, item, item.receipt.store_name)

    # Keep the receipt total in sync with its line items.
    _sync_total(item.receipt)

    db.commit()
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/items", response_class=HTMLResponse)
def add_item(
    receipt_id: int,
    description: str = Form(...),
    amount: str = Form(...),
    category: str = Form(...),
    db: Session = Depends(get_db),
):
    receipt = _get_receipt(db, receipt_id)
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
    if not description.strip():
        raise HTTPException(status_code=400, detail="Empty description")

    parsed_amount = _parse_amount(amount)
    result = _classify(db, description, parsed_amount)
    item = LineItem(
        description=description.strip(),
        amount=parsed_amount,
        category=category,
        product=result.product if result else None,
    )
    receipt.items.append(item)
    _sync_total(receipt)
    # A receipt the model couldn't read counts as done once filled in by hand.
    if receipt.status == ReceiptStatus.FAILED:
        receipt.status = ReceiptStatus.PROCESSED
        receipt.error_message = None
    db.commit()
    return RedirectResponse(url=f"/receipts/{receipt_id}#item-{item.id}", status_code=303)


@router.post("/receipts/{receipt_id}/items/{item_id}/delete", response_class=HTMLResponse)
def delete_item(receipt_id: int, item_id: int, db: Session = Depends(get_db)):
    item = db.get(LineItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(status_code=404, detail="Item not found")
    receipt = item.receipt
    receipt.items.remove(item)
    _sync_total(receipt)
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
    day = _receipt_day(receipt)
    db.delete(receipt)
    db.commit()
    return RedirectResponse(url=f"/?month={day:%Y-%m}", status_code=303)
