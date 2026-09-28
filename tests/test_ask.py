from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import ask


def _add(day, store, amount, category):
    from app.database import SessionLocal
    from app.models import LineItem, Receipt, ReceiptStatus

    with SessionLocal() as db:
        receipt = Receipt(status=ReceiptStatus.PROCESSED, store_name=store, purchase_date=day, total_amount=amount)
        receipt.items.append(LineItem(description=store, amount=amount, category=category))
        db.add(receipt)
        db.commit()


def test_run_sql_reads(client):
    _add(date(2026, 8, 1), "Wolt", 12.5, "Кафе/рестораны")
    result = ask.run_sql("SELECT SUM(amount) AS total FROM line_items;")
    assert result == {"columns": ["total"], "rows": [(12.5,)], "truncated": False}


def test_run_sql_cannot_write(client):
    _add(date(2026, 8, 1), "Wolt", 12.5, "Кафе/рестораны")
    assert "error" in ask.run_sql("DELETE FROM line_items")
    assert "error" in ask.run_sql("SELECT 1; DELETE FROM line_items")
    # Even a write disguised as a CTE is refused by the read-only connection.
    assert "error" in ask.run_sql("WITH x AS (SELECT 1) UPDATE line_items SET amount = 0")
    assert ask.run_sql("SELECT amount FROM line_items")["rows"] == [(12.5,)]


def test_run_sql_reports_bad_query(client):
    assert "no such table" in ask.run_sql("SELECT * FROM nope")["error"]


def _response(content, stop_reason):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, model="claude-sonnet-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def test_ask_runs_tool_loop_and_answers(logged_in_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()
    _add(date(2026, 8, 1), "Wolt", 12.5, "Кафе/рестораны")

    tool_call = SimpleNamespace(type="tool_use", id="t1", input={"query": "SELECT SUM(amount) FROM line_items"})
    final = SimpleNamespace(type="text", text="Ты потратил **12,50 €**.\n- Wolt: 12,50 €")
    client = MagicMock()
    client.messages.create.side_effect = [_response([tool_call], "tool_use"), _response([final], "end_turn")]

    with patch("app.ask.Anthropic", return_value=client):
        page = logged_in_client.post("/ask", data={"question": "Сколько на кафе?"}).text

    assert "<b>12,50 €</b>" in page and "<li>Wolt: 12,50 €</li>" in page
    second_call = client.messages.create.call_args_list[1].kwargs
    tool_result = second_call["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and "12.5" in tool_result["content"]
    assert second_call["model"] == "claude-sonnet-5"
    assert second_call["output_config"] == {"effort": "medium"}

    from app.database import SessionLocal
    from app.models import ApiCall

    with SessionLocal() as db:
        assert [c.purpose for c in db.query(ApiCall)] == ["ask", "ask"]


def test_ask_without_key_shows_error(logged_in_client):
    assert "ANTHROPIC_API_KEY" in logged_in_client.post("/ask", data={"question": "?"}).text
