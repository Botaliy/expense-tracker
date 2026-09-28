"""Questions about spending in plain words: "how much did cafés cost this summer?".

Claude gets the database schema and one tool, ``run_sql``, and writes the
queries itself. The tool opens its own read-only connection (``mode=ro`` and
``PRAGMA query_only``), accepts a single SELECT, caps the rows returned and
aborts queries that run too long, so a question can read anything but change
nothing.
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from anthropic import Anthropic

from app import usage
from app.categories import CATEGORIES
from app.config import BASE_DIR, get_settings
from app.stats import MONTHS_GENITIVE

MAX_ROWS = 200
QUERY_SECONDS = 3
# Tool rounds per question; plenty for "query, look, refine, answer".
MAX_ROUNDS = 8

SCHEMA = f"""SQLite database of one person's expenses. All money is in euros unless receipts.currency says otherwise.

receipts: one purchase (a photographed receipt, a hand-entered expense, or a bank statement line)
  id INTEGER, store_name TEXT (shop/payee as customers know it, may be NULL),
  purchase_date DATE (may be NULL), uploaded_at DATETIME (UTC),
  total_amount REAL, currency TEXT (NULL or 'EUR' mean euros),
  status TEXT ('PROCESSED', 'PENDING', 'FAILED': only PROCESSED ones count),
  image_path TEXT (NULL for hand-entered and bank-imported expenses),
  source TEXT ('bank' for expenses imported from a bank statement, else NULL)

line_items: what was bought; spending is always summed from here
  id INTEGER, receipt_id INTEGER -> receipts.id,
  description TEXT (as printed on the receipt, often Greek/English/abbreviated; Russian if typed by hand),
  amount REAL (paid for this line, VAT included; negative for discounts),
  quantity REAL (NULL = 1; kilograms for weighed goods),
  category TEXT (one of: {", ".join(CATEGORIES)}),
  product TEXT (short generic English label shared across receipts: 'milk', 'coffee to go', 'cat food', 'fuel'; may be NULL)

budgets: category TEXT, monthly_limit REAL

Rules:
- The date of a purchase is COALESCE(receipts.purchase_date, date(receipts.uploaded_at)).
- Spending = SUM(line_items.amount) joined to receipts with status = 'PROCESSED'.
- Match products with product LIKE '%...%' and also description/store_name when useful;
  LIKE is case-insensitive only for ASCII.
"""

SYSTEM = (
    "You answer questions about the user's own spending, using the run_sql tool to query "
    "their expense database. Look at the data before answering; never guess numbers. "
    "If a question is ambiguous (which period?), pick the most natural reading and say which "
    "one you used. Answer in Russian, briefly: the direct answer first, then a few supporting "
    "numbers or a short list if it helps. Format money like '1 234,56 €'. Plain text with "
    "simple '- ' bullet lines and **bold**; no tables, no headings, no SQL in the answer.\n\n"
    + SCHEMA
)

SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Run one read-only SQLite SELECT (or WITH … SELECT) against the expense database. "
        f"Returns up to {MAX_ROWS} rows as JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
        "required": ["query"],
    },
}


class AskError(RuntimeError):
    pass


def _db_file() -> Path:
    return (BASE_DIR / get_settings().db_path).resolve()


def run_sql(query: str, db_file: Path | None = None) -> dict:
    """Execute a read-only query; errors come back as data for the model to fix."""
    text = query.strip().rstrip(";").strip()
    if not text.lower().startswith(("select", "with")):
        return {"error": "Only a single SELECT (or WITH … SELECT) is allowed."}
    if ";" in text:
        return {"error": "Only one statement per call."}
    conn = sqlite3.connect(f"file:{db_file or _db_file()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + QUERY_SECONDS
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        cursor = conn.execute(text)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        columns = [c[0] for c in cursor.description or []]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    return {"columns": columns, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


@dataclass
class Answer:
    text: str
    queries: list[str] = field(default_factory=list)


def ask(question: str, today: date | None = None) -> Answer:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AskError("ANTHROPIC_API_KEY не настроен")
    today = today or date.today()
    client = Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict] = [{
        "role": "user",
        "content": f"Сегодня {today.day} {MONTHS_GENITIVE[today.month - 1]} {today.year} "
        f"({today.isoformat()}).\n\n{question.strip()}",
    }]
    queries: list[str] = []

    for _ in range(MAX_ROUNDS):
        try:
            response = client.messages.create(
                model=settings.ask_model,
                max_tokens=16000,
                system=SYSTEM,
                tools=[SQL_TOOL],
                messages=messages,
                # Schema and instructions are the same every time: cache them.
                cache_control={"type": "ephemeral"},
                # Simple SQL over two tables doesn't need deep reasoning; medium
                # keeps thinking (and output tokens) short.
                output_config={"effort": settings.ask_effort},
            )
        except Exception as exc:  # network/auth/rate-limit errors from the SDK
            raise AskError(f"Не удалось связаться с Claude: {exc}") from exc
        usage.record_call(usage.ASK, response)

        if response.stop_reason == "refusal":
            raise AskError("Claude отказался отвечать на этот вопрос")
        if response.stop_reason != "tool_use":
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return Answer(text=text or "Не получилось ответить.", queries=queries)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            query = str(block.input.get("query", ""))
            queries.append(query)
            result = run_sql(query)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
                "is_error": "error" in result,
            })
        messages.append({"role": "user", "content": results})

    raise AskError("Вопрос оказался слишком сложным, попробуй сформулировать проще")
