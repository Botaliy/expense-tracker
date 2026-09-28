import io
from unittest.mock import patch

from app.schemas import ExtractedLineItem, ExtractedReceipt


def test_dashboard_aggregates_by_category(logged_in_client):
    extraction = ExtractedReceipt(
        store_name="Магазин",
        purchase_date="2026-08-05",
        currency="RUB",
        total_amount=150.0,
        items=[
            ExtractedLineItem(description="Молоко", amount=100.0, category="Продукты"),
            ExtractedLineItem(description="Такси", amount=50.0, category="Транспорт"),
        ],
    )
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        logged_in_client.post(
            "/receipts",
            files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")},
        )

    resp = logged_in_client.get("/dashboard", params={"month": "2026-08"})
    assert resp.status_code == 200
    assert "Продукты" in resp.text
    assert "Транспорт" in resp.text
    assert "150\u00a0₽" in resp.text


def test_dashboard_empty_month(logged_in_client):
    resp = logged_in_client.get("/dashboard", params={"month": "2020-01"})
    assert resp.status_code == 200
    assert "Нет данных" in resp.text


def test_dashboard_includes_expense_without_purchase_date(logged_in_client):
    from datetime import date

    logged_in_client.post(
        "/expenses",
        data={"description": "Без даты", "amount": "77", "category": "Прочее"},
    )
    resp = logged_in_client.get(
        "/dashboard", params={"month": date.today().strftime("%Y-%m")}
    )
    assert "Без даты" in resp.text
    assert "77\u00a0₽" in resp.text


def test_dashboard_invalid_month_falls_back(logged_in_client):
    for bad in ("abc", "2026-13", "2026"):
        resp = logged_in_client.get("/dashboard", params={"month": bad})
        assert resp.status_code == 200


def _add(client, amount, category, day, description="Трата"):
    client.post(
        "/expenses",
        data={
            "description": description,
            "amount": amount,
            "category": category,
            "purchase_date": day.isoformat(),
        },
    )


def test_dashboard_months_chart_and_category_deltas(logged_in_client):
    from datetime import date

    _add(logged_in_client, "1000", "Продукты", date(2026, 7, 5))
    _add(logged_in_client, "3000", "Продукты", date(2026, 8, 5))
    _add(logged_in_client, "500", "Кафе/рестораны", date(2026, 8, 6))
    _add(logged_in_client, "2000", "Продукты", date(2026, 9, 5))
    _add(logged_in_client, "700", "Кафе/рестораны", date(2026, 9, 6))
    _add(logged_in_client, "300", "Техника", date(2026, 9, 7))

    page = logged_in_client.get("/dashboard", params={"month": "2026-09"}).text

    # Six columns ending with the selected month, which is marked current.
    for short in ("апр", "май", "июн", "июл", "авг", "сен"):
        assert f">{short}<" in page
    assert 'aria-label="Август 2026: 3 500 ₽"' in page  # striptags normalises spaces
    assert 'aria-current="page"' in page
    # Per-category change vs previous month.
    assert "↓ −1\u00a0000\u00a0₽" in page  # Продукты 3000 → 2000
    assert "↑ +200\u00a0₽" in page  # Кафе 500 → 700
    assert "в прошлом месяце не было" in page  # Техника


def test_monthly_totals_and_ticks():
    from app.stats import nice_ticks
    from app.templating import compact_number

    assert nice_ticks(0) == [0]
    assert nice_ticks(10805.4) == [0, 5000, 10000, 15000]
    assert nice_ticks(870) == [0, 250, 500, 750, 1000]
    assert [compact_number(t) for t in (0, 750, 2500, 20000)] == ["0", "750", "2,5\u00a0тыс", "20\u00a0тыс"]
