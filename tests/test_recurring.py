from datetime import date

from app import recurring
from tests.factories import add_expense, months_back, session


def test_monthly_payment_detected_and_missing_one_flagged(client):
    today = date(2026, 9, 20)
    for n in (1, 2, 3, 4):
        y, m = months_back(today, n)
        add_expense(date(y, m, 5), "Cyta", 35.99, "Связь/интернет", source="bank")
        # Groceries several times a month are not a regular payment.
        for d in (3, 11, 19):
            add_expense(date(y, m, d), "Lidl", 40 + d, "Продукты")
    with session() as db:
        items = recurring.detect(db, today)
    assert [(r.name, r.day, r.status) for r in items] == [("Cyta", 5, "late")]

    add_expense(date(2026, 9, 6), "Cyta", 35.99, "Связь/интернет")
    with session() as db:
        assert recurring.detect(db, today)[0].status == "paid"
        assert recurring.reminders(recurring.detect(db, today)) == []


def test_recurring_can_be_hidden(logged_in_client):
    today = date.today()
    for n in (1, 2, 3):
        y, m = months_back(today, n)
        add_expense(date(y, m, 1), "Netflix", 12.99, "Развлечения")
    assert "Netflix" in logged_in_client.get("/recurring").text
    logged_in_client.post("/recurring/hide", data={"key": "netflix"})
    assert "Netflix" not in logged_in_client.get("/recurring").text


def test_recurring_page_renders(logged_in_client):
    assert logged_in_client.get("/recurring").status_code == 200
