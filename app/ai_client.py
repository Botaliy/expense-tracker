import base64
import io
import mimetypes
from pathlib import Path

from anthropic import Anthropic
from PIL import Image, ImageOps, UnidentifiedImageError

from app.categories import (
    CATEGORIES,
    CATEGORY_GUIDANCE,
    DEFAULT_CATEGORY,
    normalize_category,
)
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
    f"{CATEGORY_GUIDANCE} "
    "If you can't confidently split into line items, return a single item summarizing "
    "the whole receipt. Use the record_receipt tool to report the result. "
    "Amounts should be plain numbers without currency symbols."
)


CATEGORIZE_TOOL_NAME = "pick_category"

CATEGORIZE_TOOL = {
    "name": CATEGORIZE_TOOL_NAME,
    "description": "Report the single best-matching category for one expense.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": CATEGORIES,
                "description": "Best matching category from the fixed list.",
            }
        },
        "required": ["category"],
    },
}

CATEGORIZE_PROMPT = (
    "You classify a single personal expense into exactly one category from this "
    f"fixed list: {', '.join(CATEGORIES)}. "
    f"{CATEGORY_GUIDANCE} "
    f"If nothing fits confidently, use '{DEFAULT_CATEGORY}'. "
    "Use the pick_category tool to report the result."
)


class ReceiptExtractionError(RuntimeError):
    pass


class ExpenseCategorizationError(RuntimeError):
    pass


# The model downscales anything larger anyway, and the API rejects images over
# 5 MB — phone photos routinely exceed both, so shrink before sending.
MAX_IMAGE_EDGE = 1568


def _prepare_image(image_path: Path) -> tuple[str, bytes]:
    raw = image_path.read_bytes()
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            return "image/jpeg", buf.getvalue()
    except (UnidentifiedImageError, OSError):
        # Not something Pillow can read — send as-is and let the API decide.
        return mimetypes.guess_type(image_path.name)[0] or "image/jpeg", raw


def _encode_image(image_path: Path) -> tuple[str, str]:
    media_type, content = _prepare_image(image_path)
    return media_type, base64.standard_b64encode(content).decode("utf-8")


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


def categorize_expense(description: str, amount: float | None = None) -> str:
    """Ask the model to pick one category for a single hand-entered expense.

    Returns a category guaranteed to be in ``CATEGORIES``. Raises
    ``ExpenseCategorizationError`` if the API is unavailable or misbehaves.
    """
    if not description or not description.strip():
        raise ExpenseCategorizationError("Description is empty")

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExpenseCategorizationError("ANTHROPIC_API_KEY is not configured")

    client = Anthropic(api_key=settings.anthropic_api_key)

    expense_line = f"Expense: {description.strip()}"
    if amount is not None:
        expense_line += f"\nAmount: {amount:g}"

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=256,
            tools=[CATEGORIZE_TOOL],
            tool_choice={"type": "tool", "name": CATEGORIZE_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": f"{CATEGORIZE_PROMPT}\n\n{expense_line}",
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ExpenseCategorizationError(f"Anthropic API call failed: {exc}") from exc

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ExpenseCategorizationError("Model did not return a category")

    return normalize_category(tool_use_block.input.get("category"))
