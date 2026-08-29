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

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        receipt = db.get(Receipt, int(receipt_id))
        item_id = receipt.items[0].id

    update_resp = logged_in_client.post(
        f"/receipts/{receipt_id}/items/{item_id}",
        data={"category": "Здоровье"},
        follow_redirects=False,
    )
    assert update_resp.status_code == 303

    with SessionLocal() as db:
        receipt = db.get(Receipt, int(receipt_id))
        assert receipt.items[0].category == "Здоровье"


def test_update_all_items_category(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = resp.headers["location"].rsplit("/", 1)[-1]

    update_resp = logged_in_client.post(
        f"/receipts/{receipt_id}/category",
        data={"category": "Развлечения"},
        follow_redirects=False,
    )
    assert update_resp.status_code == 303

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        receipt = db.get(Receipt, int(receipt_id))
        assert all(item.category == "Развлечения" for item in receipt.items)


def test_update_receipt_store_and_date(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    upd = logged_in_client.post(
        f"/receipts/{receipt_id}",
        data={"store_name": "Ашан", "purchase_date": "2026-07-01"},
        follow_redirects=False,
    )
    assert upd.status_code == 303

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        assert receipt.store_name == "Ашан"
        assert receipt.purchase_date.isoformat() == "2026-07-01"


def test_update_item_description_and_amount_syncs_total(logged_in_client):
    resp = logged_in_client.post(
        "/expenses",
        data={"description": "Кофе", "amount": "150", "category": "Кафе/рестораны"},
        follow_redirects=False,
    )
    receipt_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        item_id = db.get(Receipt, receipt_id).items[0].id

    upd = logged_in_client.post(
        f"/receipts/{receipt_id}/items/{item_id}",
        data={"description": "Капучино", "amount": "220", "category": "Кафе/рестораны"},
        follow_redirects=False,
    )
    assert upd.status_code == 303

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        assert receipt.items[0].description == "Капучино"
        assert receipt.items[0].amount == 220
        assert receipt.total_amount == 220


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


def test_receipts_list_shows_categories(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )

    listing = logged_in_client.get("/")
    assert listing.status_code == 200
    assert "Категория" in listing.text  # new column header
    assert "badge-category" in listing.text
    assert "Продукты" in listing.text
    assert "Транспорт" in listing.text


def test_add_manual_expense_auto_categorizes_when_blank(logged_in_client):
    with patch(
        "app.routers.receipts.categorize_expense", return_value="Транспорт"
    ) as mock_cat:
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Такси до аэропорта", "amount": "700", "category": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    mock_cat.assert_called_once()
    detail = logged_in_client.get(resp.headers["location"])
    assert "Транспорт" in detail.text


def test_add_manual_expense_falls_back_when_ai_unavailable(logged_in_client):
    from app.ai_client import ExpenseCategorizationError

    with patch(
        "app.routers.receipts.categorize_expense",
        side_effect=ExpenseCategorizationError("no key"),
    ):
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Что-то непонятное", "amount": "10"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    detail = logged_in_client.get(resp.headers["location"])
    assert "Прочее" in detail.text


def test_categorize_endpoint_returns_suggestion(logged_in_client):
    with patch(
        "app.routers.receipts.categorize_expense", return_value="Развлечения"
    ):
        resp = logged_in_client.post(
            "/expenses/categorize",
            data={"description": "Билеты в кино", "amount": "600"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"category": "Развлечения"}


def test_categorize_endpoint_reports_ai_error(logged_in_client):
    from app.ai_client import ExpenseCategorizationError

    with patch(
        "app.routers.receipts.categorize_expense",
        side_effect=ExpenseCategorizationError("boom"),
    ):
        resp = logged_in_client.post(
            "/expenses/categorize",
            data={"description": "Билеты в кино"},
        )
    assert resp.status_code == 502
    assert "boom" in resp.json()["error"]


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
