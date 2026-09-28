from datetime import date, timedelta

from app import prices
from tests.factories import add_expense, session


def test_price_increase_in_same_store_and_cheaper_elsewhere(client):
    today = date.today()
    for n, price in ((90, 1.00), (60, 1.05), (30, 1.00)):
        add_expense(today - timedelta(days=n), "Alphamega", price, product="milk")
    add_expense(today - timedelta(days=2), "Alphamega", 1.30, product="milk")
    add_expense(today - timedelta(days=20), "Lidl", 0.80, product="milk")
    # Weighed goods compare per kg.
    add_expense(today - timedelta(days=40), "Lidl", 3.0, product="tomatoes", quantity=1.5)
    add_expense(today - timedelta(days=10), "Alphamega", 3.0, product="tomatoes", quantity=1.0)

    with session() as db:
        r = prices.report(db, today)
    assert [(c.product, c.store) for c in r["up"]] == [("milk", "Alphamega")]
    assert round(r["up"][0].change, 2) == 0.30
    cheaper = {c.product: c for c in r["cheaper"]}
    assert cheaper["milk"].stores[0].store == "Lidl"
    assert cheaper["tomatoes"].stores[0].price == 2.0


def test_prices_page_renders(logged_in_client):
    assert "Где дешевле" in logged_in_client.get("/prices").text
