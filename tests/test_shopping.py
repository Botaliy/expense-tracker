from datetime import date, timedelta

from app import shopping
from tests.factories import add_expense, session


def test_shopping_list_forecast_tick_and_own_items(logged_in_client):
    today = date.today()
    for n in (21, 14, 7):
        add_expense(today - timedelta(days=n), "Lidl", 2.5, product="coffee beans")

    page = logged_in_client.get("/shopping").text
    assert "coffee beans" in page

    with session() as db:
        line = next(l for l in shopping.shopping_list(db) if l.prediction)
    logged_in_client.post(
        "/shopping/forecast",
        data={"product": "coffee beans", "due_date": line.prediction.due_date.isoformat(), "done": "true"},
    )
    with session() as db:
        assert next(l for l in shopping.shopping_list(db) if l.prediction).done

    # Bought again: the next due date is a new line, not ticked.
    add_expense(today, "Lidl", 2.5, product="coffee beans")
    with session() as db:
        lines = [l for l in shopping.shopping_list(db) if l.prediction]
    assert all(not l.done for l in lines)

    logged_in_client.post("/shopping/items", data={"name": "  батарейки  "})
    with session() as db:
        own = next(l for l in shopping.shopping_list(db) if not l.prediction)
    assert own.name == "батарейки"
    logged_in_client.post(f"/shopping/items/{own.item_id}", data={"done": "true"})
    logged_in_client.post("/shopping/clear")
    with session() as db:
        assert not [l for l in shopping.shopping_list(db) if not l.prediction]
