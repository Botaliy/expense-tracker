from datetime import date

from app import budgets
from tests.factories import add_expense, session


def test_budget_over_and_at_risk(client):
    today = date(2026, 9, 15)
    add_expense(date(2026, 9, 3), "Wolt", 120, "Кафе/рестораны")
    add_expense(date(2026, 9, 10), "Lidl", 200, "Продукты")
    with session() as db:
        budgets.set_limit(db, "Кафе/рестораны", 100)
        budgets.set_limit(db, "Продукты", 300)
        report = budgets.report(db, today)
    by_cat = {b.category: b for b in report["budgeted"]}
    assert by_cat["Кафе/рестораны"].status == "over"
    # 200 by the 15th → ~400 by the 30th, over the 300 limit.
    assert by_cat["Продукты"].status == "risk"
    assert [b.category for b in report["alerts"]] == ["Кафе/рестораны", "Продукты"]


def test_budget_page_sets_and_removes_limit(logged_in_client):
    logged_in_client.post("/budgets", data={"category": "Транспорт", "limit": "1 200,50"})
    with session() as db:
        assert budgets.limits(db) == {"Транспорт": 1200.5}
    assert "Транспорт" in logged_in_client.get("/budgets").text
    logged_in_client.post("/budgets", data={"category": "Транспорт", "limit": ""})
    with session() as db:
        assert budgets.limits(db) == {}


def test_suggested_limit_rounds_up():
    line = budgets.BudgetLine("Продукты", None, 0, None, average=433.2)
    assert line.suggested == 450


def test_dashboard_shows_budget_alert(logged_in_client):
    today = date.today()
    add_expense(today, "Wolt", 50, "Кафе/рестораны")
    logged_in_client.post("/budgets", data={"category": "Кафе/рестораны", "limit": "10"})
    assert "Обрати внимание" in logged_in_client.get("/dashboard").text


def test_budgets_page_renders(logged_in_client):
    assert logged_in_client.get("/budgets").status_code == 200
