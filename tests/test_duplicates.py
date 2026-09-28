import io
import re
from unittest.mock import patch

from app.schemas import ExtractedLineItem, ExtractedReceipt


def _rid(resp) -> int:
    return int(re.search(r"(?:#r-|/receipts/)(\d+)", resp.headers["location"]).group(1))


def _extraction(total=5.0):
    return ExtractedReceipt(
        store_name="Alphamega",
        purchase_date="2026-08-10",
        currency="EUR",
        total_amount=total,
        items=[ExtractedLineItem(description="ΓΑΛΑ 1L", amount=total, category="Прочее", product="juice")],
    )


def _upload(client, content=b"img", extraction=None):
    with patch("app.receipts.extract_receipt_data", return_value=extraction or _extraction()) as m:
        resp = client.post(
            "/receipts", files={"file": ("r.jpg", io.BytesIO(content), "image/jpeg")}, follow_redirects=False
        )
    return resp, m


def _items():
    from app.database import SessionLocal
    from app.models import LineItem

    with SessionLocal() as db:
        return [(i.receipt_id, i.category, i.product) for i in db.query(LineItem).order_by(LineItem.id)]


def test_same_photo_is_not_recognized_twice(logged_in_client):
    first, _ = _upload(logged_in_client, b"same-bytes")
    second, model = _upload(logged_in_client, b"same-bytes")
    model.assert_not_called()
    assert second.headers["location"] == f"/receipts/{_rid(first)}?dup=photo"
    assert "уже загружено" in logged_in_client.get(second.headers["location"]).text


def test_lookalike_receipt_is_flagged_and_can_be_dismissed(logged_in_client):
    first, _ = _upload(logged_in_client, b"photo-a")
    second, _ = _upload(logged_in_client, b"photo-b")
    rid = _rid(second)
    assert "Похоже на дубль" in logged_in_client.get(f"/receipts/{rid}").text
    assert "дубль?" in logged_in_client.get("/?month=2026-08").text

    logged_in_client.post(f"/receipts/{rid}/not-duplicate")
    assert "Похоже на дубль" not in logged_in_client.get(f"/receipts/{rid}").text
    assert "Похоже на дубль" not in logged_in_client.get(f"/receipts/{_rid(first)}").text
    assert "дубль?" not in logged_in_client.get("/?month=2026-08").text
