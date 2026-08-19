import base64
import mimetypes
from pathlib import Path

from anthropic import Anthropic

from app.categories import CATEGORIES
from app.config import get_settings
from app.schemas import ExtractedReceipt

EXTRACT_TOOL_NAME = "record_receipt"

EXTRACT_TOOL = {
    "name": EXTRACT_TOOL_NAME,
    "description": "Record structured data extracted from a receipt photo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "store_name": {"type": "string", "description": "Name of the store/merchant, if visible."},
            "purchase_date": {
                "type": "string",
                "description": "Date of purchase in ISO 8601 (YYYY-MM-DD), if visible.",
            },
            "currency": {
                "type": "string",
                "description": "Currency code or symbol as shown on the receipt (e.g. RUB, USD, EUR).",
            },
            "total_amount": {"type": "number", "description": "Total amount paid, if visible."},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "amount": {"type": "number", "description": "Line total for this item."},
                        "quantity": {"type": "number"},
                        "category": {
                            "type": "string",
                            "enum": CATEGORIES,
                            "description": "Best matching category from the fixed list.",
                        },
                    },
                    "required": ["description", "amount", "category"],
                },
            },
        },
        "required": ["items"],
    },
}

PROMPT = (
    "You are extracting structured data from a photo of a purchase receipt. "
    "Read every line item, its price, and assign each item the closest matching "
    f"category from this fixed list: {', '.join(CATEGORIES)}. "
    "Category disambiguation notes: "
    "'Алкоголь' is any alcoholic drink (beer, wine, spirits, etc.), even if bought in a "
    "grocery store — it never goes into 'Продукты'. "
    "'Техника' covers electronics and gaming: computer/console hardware and accessories, "
    "software, and video games. "
    "'Красота' covers cosmetics and personal grooming services: makeup/skincare products, "
    "manicure, pedicure, hairdresser/barber — as opposed to 'Здоровье', which is for "
    "medicine, medical services, and health-related purchases. "
    "'Авто' is for car-related expenses: fuel, parking, maintenance, car parts — as opposed "
    "to 'Транспорт', which is for public/shared transport (taxi, bus, metro, etc.). "
    "If you can't confidently split into line items, return a single item summarizing "
    "the whole receipt. Use the record_receipt tool to report the result. "
    "Amounts should be plain numbers without currency symbols."
)


class ReceiptExtractionError(RuntimeError):
    pass


def _encode_image(image_path: Path) -> tuple[str, str]:
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")
    return media_type, data


def extract_receipt_data(image_path: Path) -> ExtractedReceipt:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ReceiptExtractionError("ANTHROPIC_API_KEY is not configured")

    media_type, data = _encode_image(image_path)
    client = Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": EXTRACT_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ReceiptExtractionError(f"Anthropic API call failed: {exc}") from exc

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ReceiptExtractionError("Model did not return structured data")

    try:
        return ExtractedReceipt.model_validate(tool_use_block.input)
    except Exception as exc:
        raise ReceiptExtractionError(f"Could not parse model output: {exc}") from exc
