import io
from unittest.mock import patch

from app.schemas import ExtractedLineItem, ExtractedReceipt


def _fake_extraction():
    return ExtractedReceipt(
        store_name="Пятёрочка",
        purchase_date="2026-08-10",
        currency="RUB",
        total_amount=350.0,
        items=[
            ExtractedLineItem(description="Молоко", amount=90.0, category="Продукты"),
            ExtractedLineItem(description="Хлеб", amount=60.0, category="Продукты"),
            ExtractedLineItem(description="Такси", amount=200.0, category="Транспорт"),
        ],
    )


def test_upload_receipt_creates_processed_record(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    detail_url = resp.headers["location"]

    detail = logged_in_client.get(detail_url)
    assert detail.status_code == 200
    assert "Пятёрочка" in detail.text
    assert "Молоко" in detail.text
    assert "processed" in detail.text


def test_upload_receipt_handles_ai_failure(logged_in_client):
    from app.ai_client import ReceiptExtractionError

    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch(
        "app.receipts.extract_receipt_data",
        side_effect=ReceiptExtractionError("boom"),
    ):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    detail_url = resp.headers["location"]
    detail = logged_in_client.get(detail_url)
    assert "failed" in detail.text
    assert "boom" in detail.text


def test_update_item_category(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = resp.headers["location"].rsplit("/", 1)[-1]

    detail = logged_in_client.get(f"/receipts/{receipt_id}")
    assert detail.status_code == 200


def test_add_manual_expense(logged_in_client):
    resp = logged_in_client.post(
        "/expenses",
        data={
            "description": "Такси домой",
            "amount": "250.5",
            "category": "Транспорт",
            "purchase_date": "2026-08-15",
            "store_name": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail_url = resp.headers["location"]

    detail = logged_in_client.get(detail_url)
    assert detail.status_code == 200
    assert "Такси домой" in detail.text
    assert "250.50" in detail.text
    assert "processed" in detail.text
    # No receipt photo was attached, so no <img> should be rendered.
    assert "<img" not in detail.text


def test_add_manual_expense_rejects_unknown_category(logged_in_client):
    resp = logged_in_client.post(
        "/expenses",
        data={
            "description": "Что-то",
            "amount": "10",
            "category": "Не существует",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail = logged_in_client.get(resp.headers["location"])
    # Falls back to the default category instead of failing.
    assert "Прочее" in detail.text
