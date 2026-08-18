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
    assert "150.00" in resp.text


def test_dashboard_empty_month(logged_in_client):
    resp = logged_in_client.get("/dashboard", params={"month": "2020-01"})
    assert resp.status_code == 200
    assert "Нет данных" in resp.text
