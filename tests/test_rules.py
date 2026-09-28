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


def test_correction_is_applied_to_next_receipt(logged_in_client):
    resp, _ = _upload(logged_in_client, b"one")
    rid = _rid(resp)
    item_id = int(re.search(r'id="item-(\d+)"', logged_in_client.get(f"/receipts/{rid}").text).group(1))
    logged_in_client.post(
        f"/receipts/{rid}/items/{item_id}", data={"category": "Продукты", "product": "milk"}
    )

    _upload(logged_in_client, b"two", _extraction(total=6.0))
    assert _items()[-1][1:] == ("Продукты", "milk")


def test_rule_is_per_store(logged_in_client):
    resp, _ = _upload(logged_in_client, b"one")
    rid = _rid(resp)
    item_id = int(re.search(r'id="item-(\d+)"', logged_in_client.get(f"/receipts/{rid}").text).group(1))
    logged_in_client.post(f"/receipts/{rid}/items/{item_id}", data={"category": "Продукты"})

    other = _extraction(total=7.0)
    other.store_name = "Lidl"
    _upload(logged_in_client, b"two", other)
    assert _items()[-1][1] == "Прочее"


def test_rule_answers_manual_categorize_without_model(logged_in_client):
    from app.database import SessionLocal
    from app.models import CategoryRule

    with SessionLocal() as db:
        db.add(CategoryRule(key="кофе", store_key="", description="кофе", category="Кафе/рестораны", product="coffee to go"))
        db.commit()
    with patch("app.routers.receipts.classify_expense") as model:
        resp = logged_in_client.post("/expenses/categorize", data={"description": "  Кофе "})
    model.assert_not_called()
    assert resp.json()["category"] == "Кафе/рестораны"
    assert resp.json()["source"] == "rule"


def test_more_and_rules_pages_render(logged_in_client):
    assert "Мои правила" in logged_in_client.get("/more").text
    assert logged_in_client.get("/rules").status_code == 200
