import base64
import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic
from PIL import Image, ImageOps, UnidentifiedImageError

from app.categories import (
    CATEGORIES,
    CATEGORY_GUIDANCE,
    DEFAULT_CATEGORY,
    normalize_category,
)
from app import usage
from app.config import get_settings
from app.products import PRODUCT_GUIDANCE, normalize_product, stores_prompt, vocabulary_prompt
from app.schemas import ExtractedReceipt

EXTRACT_TOOL_NAME = "record_receipt"

EXTRACT_TOOL = {
    "name": EXTRACT_TOOL_NAME,
    "description": "Record structured data extracted from a receipt photo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "store_name": {
                "type": "string",
                "description": "Shop brand as customers know it ('Alphamega', 'Mas Supermarkets'), "
                "not the legal entity, address or document title.",
            },
            "purchase_date": {
                "type": "string",
                "description": "Date of purchase in ISO 8601 (YYYY-MM-DD). Omit if no date is printed.",
            },
            "currency": {
                "type": "string",
                "description": "Currency code or symbol as shown on the receipt (e.g. EUR, USD, GBP).",
            },
            "total_amount": {"type": "number", "description": "Total amount paid, if visible."},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "amount": {
                            "type": "number",
                            "description": "What was paid for this line: after any line discount, VAT included.",
                        },
                        "quantity": {"type": "number"},
                        "category": {
                            "type": "string",
                            "enum": CATEGORIES,
                            "description": "Best matching category from the fixed list.",
                        },
                        "product": {
                            "type": "string",
                            "description": "Short generic English product name, e.g. 'coffee beans'.",
                        },
                    },
                    "required": ["description", "amount", "category", "product"],
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
    f"{PRODUCT_GUIDANCE} "
    "If you can't confidently split into line items, return a single item summarizing "
    "the whole receipt. Use the record_receipt tool to report the result. "
    "Amounts should be plain numbers without currency symbols. "
    # Each rule below fixes a mistake seen on real receipts (Cyprus, Greek/English).
    "Receipts are European: dates are written day first (08/09/2026 is 8 September "
    "2026, never 9 August). Only use a date that is printed as a date; cash register, "
    "terminal or transaction numbers are not dates — omit the date rather than guess. "
    "When a line has a discount (e.g. 'FROM 2.60 TO 2.39' or a following '-0.21' line "
    "for that item), record the discounted amount on the item itself. "
    "Amounts are what the customer paid, VAT included; if a receipt lists net prices "
    "before VAT, use the VAT-inclusive total. "
    "Copy each item's text as printed (keep Greek as Greek); don't translate or reword "
    "it — the product field is where the meaning goes. Keep prices on the line they "
    "belong to; check that the items add up to the printed total. "
    "Skip zero-price modifier lines like 'No cutlery'."
)


CLASSIFY_TOOL_NAME = "classify_expense"

CLASSIFY_TOOL = {
    "name": CLASSIFY_TOOL_NAME,
    "description": "Report the category and product name for one expense.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": CATEGORIES,
                "description": "Best matching category from the fixed list.",
            },
            "product": {
                "type": "string",
                "description": "Short generic English product name, e.g. 'taxi'.",
            },
        },
        "required": ["category", "product"],
    },
}

CLASSIFY_PROMPT = (
    "You classify a single personal expense into exactly one category from this "
    f"fixed list: {', '.join(CATEGORIES)}. "
    f"{CATEGORY_GUIDANCE} "
    f"If nothing fits confidently, use '{DEFAULT_CATEGORY}'. "
    "The expense is typed by hand, usually in Russian. "
    f"{PRODUCT_GUIDANCE} "
    "Use the classify_expense tool to report the result."
)

LABEL_TOOL_NAME = "label_products"

LABEL_TOOL = {
    "name": LABEL_TOOL_NAME,
    "description": "Report a product name for each numbered line item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "description": "The item's number."},
                        "product": {"type": "string"},
                    },
                    "required": ["n", "product"],
                },
            }
        },
        "required": ["labels"],
    },
}

LABEL_PROMPT = (
    "These are line items from someone's receipts (shop language, often abbreviated) "
    "and hand-entered expenses (usually Russian), with the shop when known. "
    f"{PRODUCT_GUIDANCE} "
    "Label every numbered item. Use the label_products tool."
)


class ReceiptExtractionError(RuntimeError):
    pass


class ExpenseCategorizationError(RuntimeError):
    pass


@dataclass
class ExpenseClassification:
    category: str
    product: str | None


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


def extract_receipt_data(
    image_path: Path,
    known: list[str] | None = None,
    receipt_id: int | None = None,
    stores: list[str] | None = None,
) -> ExtractedReceipt:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ReceiptExtractionError("ANTHROPIC_API_KEY is not configured")

    media_type, data = _encode_image(image_path)
    client = Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
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
                        {"type": "text", "text": f"{PROMPT}\n\n{vocabulary_prompt(known or [])}\n\n{stores_prompt(stores or [])}"},
                    ],
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ReceiptExtractionError(f"Anthropic API call failed: {exc}") from exc

    usage.record_call(usage.RECEIPT, response, receipt_id)

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ReceiptExtractionError("Model did not return structured data")

    try:
        return ExtractedReceipt.model_validate(tool_use_block.input)
    except Exception as exc:
        raise ReceiptExtractionError(f"Could not parse model output: {exc}") from exc


def classify_expense(
    description: str, amount: float | None = None, known: list[str] | None = None
) -> ExpenseClassification:
    """Category and product name for a single hand-entered expense.

    The category is guaranteed to be in ``CATEGORIES``. Raises
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
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": CLASSIFY_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": f"{CLASSIFY_PROMPT}\n\n{vocabulary_prompt(known or [])}\n\n{expense_line}",
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ExpenseCategorizationError(f"Anthropic API call failed: {exc}") from exc

    usage.record_call(usage.CLASSIFY, response)

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ExpenseCategorizationError("Model did not return a category")

    return ExpenseClassification(
        category=normalize_category(tool_use_block.input.get("category")),
        product=normalize_product(tool_use_block.input.get("product")),
    )


def label_products(
    items: list[tuple[str, str | None]], known: list[str] | None = None
) -> list[str | None]:
    """Product names for (description, store) pairs, in the same order.

    Text only, no photos — used to label items recorded before products existed.
    Raises ``ExpenseCategorizationError`` on API failure.
    """
    if not items:
        return []
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExpenseCategorizationError("ANTHROPIC_API_KEY is not configured")

    numbered = "\n".join(
        f"{n}. {desc}" + (f"  [shop: {store}]" if store else "")
        for n, (desc, store) in enumerate(items, start=1)
    )
    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            tools=[LABEL_TOOL],
            tool_choice={"type": "tool", "name": LABEL_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": f"{LABEL_PROMPT}\n\n{vocabulary_prompt(known or [])}\n\n{numbered}",
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ExpenseCategorizationError(f"Anthropic API call failed: {exc}") from exc

    usage.record_call(usage.LABEL, response)

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ExpenseCategorizationError("Model did not return labels")

    by_number = {}
    for label in tool_use_block.input.get("labels") or []:
        if isinstance(label, dict) and isinstance(label.get("n"), int):
            by_number[label["n"]] = normalize_product(label.get("product"))
    return [by_number.get(n) for n in range(1, len(items) + 1)]


STATEMENT_TOOL_NAME = "label_statement"

STATEMENT_TOOL = {
    "name": STATEMENT_TOOL_NAME,
    "description": "Report category, product and shop for each numbered bank statement line.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "description": "The line's number."},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "product": {
                            "type": "string",
                            "description": "Short generic English name of what was paid for, e.g. 'groceries', 'taxi', 'rent'.",
                        },
                        "store": {
                            "type": "string",
                            "description": "Merchant as customers know it ('Alphamega', 'Wolt', 'Bolt'), "
                            "without legal suffixes, city, card or terminal numbers.",
                        },
                    },
                    "required": ["n", "category", "product", "store"],
                },
            }
        },
        "required": ["lines"],
    },
}

STATEMENT_PROMPT = (
    "These are card payments and transfers from a personal bank statement (Cyprus: "
    "Revolut or Bank of Cyprus), one merchant descriptor per numbered line. For each, "
    "pick the closest category from this fixed list: "
    f"{', '.join(CATEGORIES)}. {CATEGORY_GUIDANCE} "
    "A supermarket payment is 'Продукты' unless the merchant is clearly something else; "
    f"if nothing fits, use '{DEFAULT_CATEGORY}'. "
    f"{PRODUCT_GUIDANCE} "
    "Label every numbered line. Use the label_statement tool."
)


def classify_statement(
    descriptions: list[str], known: list[str] | None = None, stores: list[str] | None = None
) -> list["StatementLabel | None"]:
    """Category, product and clean shop name per statement line, same order.

    Raises ``ExpenseCategorizationError`` on API failure.
    """
    if not descriptions:
        return []
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExpenseCategorizationError("ANTHROPIC_API_KEY is not configured")

    numbered = "\n".join(f"{n}. {d}" for n, d in enumerate(descriptions, start=1))
    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=8192,
            tools=[STATEMENT_TOOL],
            tool_choice={"type": "tool", "name": STATEMENT_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": f"{STATEMENT_PROMPT}\n\n{vocabulary_prompt(known or [])}\n\n"
                    f"{stores_prompt(stores or [])}\n\n{numbered}",
                }
            ],
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise ExpenseCategorizationError(f"Anthropic API call failed: {exc}") from exc

    usage.record_call(usage.STATEMENT, response)

    tool_use_block = next(
        (block for block in response.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ExpenseCategorizationError("Model did not return labels")

    by_number: dict[int, StatementLabel] = {}
    for line in tool_use_block.input.get("lines") or []:
        if isinstance(line, dict) and isinstance(line.get("n"), int):
            store = (line.get("store") or "").strip() or None
            by_number[line["n"]] = StatementLabel(
                category=normalize_category(line.get("category")),
                product=normalize_product(line.get("product")),
                store=store[:255] if store else None,
            )
    return [by_number.get(n) for n in range(1, len(descriptions) + 1)]


@dataclass
class StatementLabel:
    category: str
    product: str | None
    store: str | None


COLUMNS_TOOL_NAME = "describe_columns"

COLUMNS_TOOL = {
    "name": COLUMNS_TOOL_NAME,
    "description": "Say which columns of a bank statement CSV hold what.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean", "description": "False if this isn't a list of transactions."},
            "header_row": {"type": "integer", "description": "0-based index of the header row."},
            "date": {"type": "integer", "description": "0-based column of the transaction date."},
            "description": {"type": "integer", "description": "0-based column of the merchant/description."},
            "amount": {"type": "integer", "description": "Column with a signed amount, if there is one."},
            "debit": {"type": "integer", "description": "Column with money out only, if amounts are split."},
            "currency": {"type": "integer"},
            "spent_negative": {
                "type": "boolean",
                "description": "For a signed amount column: true if money spent is negative.",
            },
        },
        "required": ["found"],
    },
}


def map_statement_columns(rows: list[list[str]]) -> dict | None:
    """Ask the model which column is which, for a layout not recognised by name.

    Returns the tool input (see COLUMNS_TOOL), or None. Only the first rows are sent.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    sample = "\n".join(f"{i}: {row}" for i, row in enumerate(rows[:20]))
    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=512,
            tools=[COLUMNS_TOOL],
            tool_choice={"type": "tool", "name": COLUMNS_TOOL_NAME},
            messages=[{
                "role": "user",
                "content": "Here are the first rows of a bank statement export, one Python list per "
                "row. Identify the header row and the columns. Use the describe_columns tool.\n\n" + sample,
            }],
        )
    except Exception:
        return None
    usage.record_call(usage.STATEMENT, response)
    block = next((b for b in response.content if b.type == "tool_use"), None)
    if block is None or not block.input.get("found"):
        return None
    return block.input
