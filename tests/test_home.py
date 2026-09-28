import io
import re
from datetime import date, timedelta
from unittest.mock import patch

from app.schemas import ExtractedLineItem, ExtractedReceipt


def _receipt_id(resp) -> int:
    return int(re.search(r"#r-(\d+)$", resp.headers["location"]).group(1))


def _add(client, amount, description="Кофе", category="Кафе/рестораны", day=None, store=""):
    return client.post(
        "/expenses",
        data={
            "description": description,
            "amount": amount,
            "category": category,
            "purchase_date": (day or date.today()).isoformat(),
            "store_name": store,
        },
        follow_redirects=False,
    )


def test_feed_groups_by_day_with_formatted_amounts(logged_in_client):
    _add(logged_in_client, "1890", "Продукты на неделю", "Продукты", store="Пятёрочка")
    _add(logged_in_client, "450", "Такси домой", "Транспорт")

    page = logged_in_client.get("/").text

    assert "Сегодня" in page
    assert "Пятёрочка" in page
    assert "Такси домой" in page
    assert "1 890 €" in page
    assert "2 340 €" in page  # day subtotal and month total
    assert "🛒" in page and "🚕" in page
    assert f"на {date.today().day} " in page


def test_manual_expense_accepts_decimal_comma(logged_in_client):
    resp = _add(logged_in_client, "199,90")
    assert resp.status_code == 303

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        assert db.get(Receipt, _receipt_id(resp)).total_amount == 199.9


def test_manual_expense_rejects_garbage_amount(logged_in_client):
    assert _add(logged_in_client, "abc").status_code == 400


def test_manual_expense_redirects_to_its_month(logged_in_client):
    resp = _add(logged_in_client, "100", day=date(2026, 3, 15))
    assert resp.headers["location"].startswith("/?month=2026-03#r-")


def test_month_navigation_filters_feed(logged_in_client):
    _add(logged_in_client, "100", "Мартовская трата", day=date(2026, 3, 15))

    march = logged_in_client.get("/", params={"month": "2026-03"}).text
    assert "Мартовская трата" in march
    assert "Март 2026" in march
    assert "/?month=2026-02" in march

    this_month = logged_in_client.get("/").text
    assert "Мартовская трата" not in this_month
    assert "В этом месяце трат нет" in this_month


def test_summary_compares_with_previous_month(logged_in_client):
    _add(logged_in_client, "1000", day=date(2026, 2, 10))
    _add(logged_in_client, "500", day=date(2026, 3, 10))

    page = logged_in_client.get("/", params={"month": "2026-03"}).text
    assert "↓ 50% к февралю" in page


def test_pending_receipt_polls_until_done(logged_in_client):
    from app.database import SessionLocal
    from app.models import Receipt, ReceiptStatus

    # Recognition runs in a background task; make it fail so the receipt
    # exists, then force it back to pending to render that state.
    from app.ai_client import ReceiptExtractionError

    with patch("app.receipts.extract_receipt_data", side_effect=ReceiptExtractionError("x")):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = _receipt_id(resp)

    page = logged_in_client.get("/").text
    assert "Чек не распознан" in page
    assert 'hx-trigger="every 2s"' not in page

    with SessionLocal() as db:
        db.get(Receipt, receipt_id).status = ReceiptStatus.PENDING
        db.commit()
    page = logged_in_client.get("/").text
    assert "Распознаю чек" in page
    assert 'hx-trigger="every 2s"' in page


def test_retry_from_feed_reprocesses_and_returns_to_feed(logged_in_client):
    from app.ai_client import ReceiptExtractionError

    with patch("app.receipts.extract_receipt_data", side_effect=ReceiptExtractionError("x")):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
    receipt_id = _receipt_id(resp)

    extraction = ExtractedReceipt(
        store_name="Магнит",
        items=[ExtractedLineItem(description="Хлеб", amount=60, category="Продукты")],
    )
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        retry = logged_in_client.post(
            f"/receipts/{receipt_id}/retry",
            data={"next": f"/?#r-{receipt_id}"},
            follow_redirects=False,
        )
    assert retry.headers["location"] == f"/?#r-{receipt_id}"
    assert "Магнит" in logged_in_client.get("/").text


def test_retry_ignores_external_redirect(logged_in_client):
    from app.ai_client import ReceiptExtractionError

    with patch("app.receipts.extract_receipt_data", side_effect=ReceiptExtractionError("x")):
        resp = logged_in_client.post(
            "/receipts",
            files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")},
            follow_redirects=False,
        )
        retry = logged_in_client.post(
            f"/receipts/{_receipt_id(resp)}/retry",
            data={"next": "//evil.example"},
            follow_redirects=False,
        )
    assert retry.headers["location"] == f"/receipts/{_receipt_id(resp)}"


def test_retry_rejects_manual_expense(logged_in_client):
    resp = _add(logged_in_client, "10")
    retry = logged_in_client.post(f"/receipts/{_receipt_id(resp)}/retry")
    assert retry.status_code == 400


def test_interrupted_receipts_are_failed_on_startup(logged_in_client):
    from app.database import SessionLocal
    from app.models import Receipt, ReceiptStatus
    from app.receipts import fail_interrupted_receipts

    with SessionLocal() as db:
        receipt = Receipt(image_path="x.jpg", status=ReceiptStatus.PENDING)
        db.add(receipt)
        db.commit()
        fail_interrupted_receipts(db)
        db.refresh(receipt)
        assert receipt.status == ReceiptStatus.FAILED


def test_money_filter():
    from app.templating import money

    assert money(1890) == "1 890 €"
    assert money(2315.4) == '2 315<span class="cents">,40</span> €'
    assert money(-30) == "−30 €"
    assert money(5, "USD") == "5 USD"
    assert money(5, "eur") == "5\u00a0€"
    assert money(None) == "—"


def test_human_date():
    from app.templating import human_date

    today = date.today()
    assert human_date(today) == "Сегодня"
    assert human_date(today - timedelta(days=1)) == "Вчера"
    assert human_date(date(2026, 9, 26)) == "сб, 26 сен"
