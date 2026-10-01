import io
import re
from unittest.mock import patch

from app.ai_client import ExpenseClassification
from app.schemas import ExtractedLineItem, ExtractedReceipt


def _receipt_id(resp) -> int:
    """Uploads and manual expenses redirect to the feed, anchored at the new row."""
    return int(re.search(r"#r-(\d+)$", resp.headers["location"]).group(1))


def _fake_extraction():
    return ExtractedReceipt(
        store_name="Пятёрочка",
        purchase_date="2026-08-10",
        currency="EUR",
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
    detail_url = f"/receipts/{_receipt_id(resp)}"

    detail = logged_in_client.get(detail_url)
    assert detail.status_code == 200
    assert "Пятёрочка" in detail.text
    assert "Молоко" in detail.text
    assert "распознано по фото" in detail.text


def test_printed_european_date_overrides_swapped_iso_date(logged_in_client):
    from datetime import date

    from app.database import SessionLocal
    from app.models import Receipt

    extraction = _fake_extraction().model_copy(update={
        "purchase_date": "2026-01-10",
        "purchase_date_text": "01/10/26 09:41",
    })
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", io.BytesIO(b"date-order"), "image/jpeg")},
            follow_redirects=False,
        )
    with SessionLocal() as db:
        assert db.get(Receipt, _receipt_id(resp)).purchase_date == date(2026, 10, 1)


def test_ambiguous_date_uses_upload_day_only_for_strong_match():
    from datetime import date

    from app.receipts import _parse_date

    uploaded_on = date(2026, 10, 1)
    assert _parse_date("2026-01-10", uploaded_on=uploaded_on) == uploaded_on
    assert _parse_date("2026-01-10", "10/01/26", uploaded_on) == uploaded_on
    assert _parse_date("2026-01-10", "01/10/26", uploaded_on) == uploaded_on
    # Neither interpretation close to the upload: retain the printed date.
    assert _parse_date("2026-01-10", "10/01/26", date(2026, 6, 1)) == date(2026, 1, 10)
    # A recent alternative is not enough if the first date is also plausible.
    assert _parse_date("2026-08-10", uploaded_on=date(2026, 10, 8)) == date(2026, 8, 10)
    assert _parse_date("2026-10-13", uploaded_on=uploaded_on) == date(2026, 10, 13)


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
    detail_url = f"/receipts/{_receipt_id(resp)}"
    detail = logged_in_client.get(detail_url)
    assert "не смог распознать" in detail.text
    assert "boom" in detail.text


def test_update_item_category(logged_in_client):
    fake_image = io.BytesIO(b"fake-image-bytes")
    with patch("app.receipts.extract_receipt_data", return_value=_fake_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", fake_image, "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = _receipt_id(resp)

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
    receipt_id = _receipt_id(resp)

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
    receipt_id = _receipt_id(resp)

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
    receipt_id = _receipt_id(resp)

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
    detail_url = f"/receipts/{_receipt_id(resp)}"

    detail = logged_in_client.get(detail_url)
    assert detail.status_code == 200
    assert "Такси домой" in detail.text
    assert "250,50" in detail.text
    assert "добавлено вручную" in detail.text
    # No receipt photo was attached, so no <img> should be rendered.
    assert "<img" not in detail.text


def test_add_manual_expense_auto_categorizes_when_blank(logged_in_client):
    with patch(
        "app.routers.receipts.classify_expense",
        return_value=ExpenseClassification("Транспорт", "taxi"),
    ) as mock_cat:
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Такси до аэропорта", "amount": "700", "category": ""},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    mock_cat.assert_called_once()
    detail = logged_in_client.get(f"/receipts/{_receipt_id(resp)}")
    assert "Транспорт" in detail.text


def test_add_manual_expense_falls_back_when_ai_unavailable(logged_in_client):
    from app.ai_client import ExpenseCategorizationError

    with patch(
        "app.routers.receipts.classify_expense",
        side_effect=ExpenseCategorizationError("no key"),
    ):
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Что-то непонятное", "amount": "10"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    detail = logged_in_client.get(f"/receipts/{_receipt_id(resp)}")
    assert "Прочее" in detail.text


def test_categorize_endpoint_returns_suggestion(logged_in_client):
    with patch(
        "app.routers.receipts.classify_expense",
        return_value=ExpenseClassification("Развлечения", "cinema"),
    ):
        resp = logged_in_client.post(
            "/expenses/categorize",
            data={"description": "Билеты в кино", "amount": "600"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"category": "Развлечения", "product": "cinema"}


def test_categorize_endpoint_reports_ai_error(logged_in_client):
    from app.ai_client import ExpenseCategorizationError

    with patch(
        "app.routers.receipts.classify_expense",
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
    detail = logged_in_client.get(f"/receipts/{_receipt_id(resp)}")
    # Falls back to the default category instead of failing.
    assert "Прочее" in detail.text


def test_upload_adds_discount_line_when_total_is_lower(logged_in_client):
    extraction = _fake_extraction().model_copy(update={"total_amount": 320.0})
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = _receipt_id(resp)

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        discount = receipt.items[-1]
        assert discount.description == "Скидка"
        assert discount.amount == -30.0
        assert discount.category == "Транспорт"  # category of the largest item
        assert receipt.total_amount == 320.0


def test_upload_without_total_uses_items_sum(logged_in_client):
    extraction = _fake_extraction().model_copy(update={"total_amount": None})
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("receipt.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = _receipt_id(resp)

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        assert len(receipt.items) == 3
        assert receipt.total_amount == 350.0


def _upload(client, extraction=None, error=None):
    from app.ai_client import ReceiptExtractionError

    kwargs = {"return_value": extraction} if extraction else {"side_effect": ReceiptExtractionError(error or "x")}
    with patch("app.receipts.extract_receipt_data", **kwargs):
        resp = client.post(
            "/receipts",
            files={"file": ("receipt.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
    return _receipt_id(resp)


def test_add_item_fills_failed_receipt(logged_in_client):
    receipt_id = _upload(logged_in_client, error="unreadable")

    resp = logged_in_client.post(
        f"/receipts/{receipt_id}/items",
        data={"description": "Продукты", "amount": "1 234,50", "category": "Продукты"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.database import SessionLocal
    from app.models import Receipt, ReceiptStatus

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        assert receipt.status == ReceiptStatus.PROCESSED
        assert receipt.error_message is None
        assert receipt.total_amount == 1234.5
        assert resp.headers["location"] == f"/receipts/{receipt_id}#item-{receipt.items[0].id}"


def test_delete_item_updates_total(logged_in_client):
    receipt_id = _upload(logged_in_client, _fake_extraction())

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        item_id = db.get(Receipt, receipt_id).items[-1].id  # Такси, 200

    resp = logged_in_client.post(f"/receipts/{receipt_id}/items/{item_id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    with SessionLocal() as db:
        receipt = db.get(Receipt, receipt_id)
        assert [i.description for i in receipt.items] == ["Молоко", "Хлеб"]
        assert receipt.total_amount == 150


def test_update_item_accepts_comma_and_unicode_minus(logged_in_client):
    receipt_id = _upload(logged_in_client, _fake_extraction())

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        item_id = db.get(Receipt, receipt_id).items[0].id

    logged_in_client.post(
        f"/receipts/{receipt_id}/items/{item_id}",
        data={"category": "Продукты", "amount": "−10,50", "quantity": "2,5"},
    )
    with SessionLocal() as db:
        item = db.get(Receipt, receipt_id).items[0]
        assert item.amount == -10.5
        assert item.quantity == 2.5


def test_delete_receipt_returns_to_its_month(logged_in_client):
    receipt_id = _upload(logged_in_client, _fake_extraction())  # dated 2026-08-10
    resp = logged_in_client.post(f"/receipts/{receipt_id}/delete", follow_redirects=False)
    assert resp.headers["location"] == "/?month=2026-08"


def test_detail_page_shows_category_split(logged_in_client):
    receipt_id = _upload(logged_in_client, _fake_extraction())
    page = logged_in_client.get(f"/receipts/{receipt_id}").text
    assert "3 позиции" in page
    assert "150 €" in page  # Продукты: 90 + 60
    assert 'href="/?month=2026-08#r-' in page


def test_cat_category_uses_drawn_icon_but_emoji_in_selects(logged_in_client):
    from app.categories import CATEGORIES, CATEGORY_GUIDANCE

    assert "Кошечка" in CATEGORIES
    assert "'Кошечка'" in CATEGORY_GUIDANCE  # the model knows what goes there

    resp = logged_in_client.post(
        "/expenses",
        data={"description": "Корм Whiskas", "amount": "18,50", "category": "Кошечка"},
        follow_redirects=False,
    )
    detail = logged_in_client.get(f"/receipts/{_receipt_id(resp)}").text
    assert 'class="cat-icon"' in detail
    assert '<option value="Кошечка" selected>🐈‍⬛ Кошечка</option>' in detail

    home = logged_in_client.get("/").text
    assert 'class="cat-icon"' in home
