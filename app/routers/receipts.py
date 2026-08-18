from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.categories import CATEGORIES
from app.database import get_db
from app.models import LineItem, Receipt
from app.receipts import create_receipt, process_receipt, save_upload

router = APIRouter(dependencies=[Depends(get_current_user)])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_receipts(request: Request, db: Session = Depends(get_db)):
    receipts = db.execute(
        select(Receipt).order_by(Receipt.uploaded_at.desc())
    ).scalars().all()
    return templates.TemplateResponse(
        request, "receipts_list.html", {"receipts": receipts}
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


@router.post("/receipts/{receipt_id}/retry", response_class=HTMLResponse)
def retry_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    process_receipt(db, receipt)
    return RedirectResponse(url=f"/receipts/{receipt_id}", status_code=303)


@router.post("/receipts/{receipt_id}/items/{item_id}", response_class=HTMLResponse)
def update_item_category(
    receipt_id: int,
    item_id: int,
    category: str,
    db: Session = Depends(get_db),
):
    item = db.get(LineItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
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
