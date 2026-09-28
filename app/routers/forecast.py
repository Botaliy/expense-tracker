from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.categories import CATEGORIES
from app.database import get_db
from app.forecast import (
    CATEGORY,
    MIN_PURCHASES,
    PRODUCT,
    add_exclusion,
    exclusions,
    predict,
    remove_exclusion,
    set_excluded_categories,
)
from app.products import known_products, normalize_product
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


def _back(next_url: str | None) -> RedirectResponse:
    # Only local paths: this comes from a form field.
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/forecast"
    return RedirectResponse(url=next_url, status_code=303)


@router.get("/forecast", response_class=HTMLResponse)
def forecast_page(request: Request, db: Session = Depends(get_db)):
    excluded = exclusions(db)
    return templates.TemplateResponse(
        request,
        "forecast.html",
        {
            "predictions": predict(db),
            "min_purchases": MIN_PURCHASES,
            "categories": CATEGORIES,
            "excluded_categories": excluded[CATEGORY],
            "excluded_products": sorted(excluded[PRODUCT]),
            "known_products": [p for p in known_products(db) if p not in excluded[PRODUCT]],
        },
    )


@router.post("/forecast/exclusions")
def exclude_product(
    product: str = Form(...),
    next: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    value = normalize_product(product)
    if value:
        add_exclusion(db, PRODUCT, value)
    return _back(next)


@router.post("/forecast/exclusions/delete")
def include_product(product: str = Form(...), db: Session = Depends(get_db)):
    remove_exclusion(db, PRODUCT, product)
    return _back("/forecast")


@router.post("/forecast/categories")
def update_categories(
    excluded: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    set_excluded_categories(db, {c for c in excluded if c in CATEGORIES})
    return _back("/forecast")
