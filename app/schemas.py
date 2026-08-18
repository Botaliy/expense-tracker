from pydantic import BaseModel, field_validator

from app.categories import normalize_category


class ExtractedLineItem(BaseModel):
    description: str
    amount: float
    quantity: float | None = None
    category: str

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        return normalize_category(value)


class ExtractedReceipt(BaseModel):
    store_name: str | None = None
    purchase_date: str | None = None  # ISO date string, parsed by caller
    currency: str | None = None
    total_amount: float | None = None
    items: list[ExtractedLineItem] = []
