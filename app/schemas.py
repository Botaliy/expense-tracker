from pydantic import BaseModel, field_validator

from app.categories import normalize_category
from app.products import normalize_product


class ExtractedLineItem(BaseModel):
    description: str
    amount: float
    quantity: float | None = None
    category: str
    product: str | None = None

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        return normalize_category(value)

    @field_validator("product")
    @classmethod
    def _normalize_product(cls, value: str | None) -> str | None:
        return normalize_product(value)


class ExtractedReceipt(BaseModel):
    store_name: str | None = None
    purchase_date: str | None = None  # ISO date string, parsed by caller
    purchase_date_text: str | None = None  # Date transcribed exactly as printed
    currency: str | None = None
    total_amount: float | None = None
    items: list[ExtractedLineItem] = []
