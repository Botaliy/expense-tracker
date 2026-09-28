import io
from unittest.mock import patch

from app.schemas import ExtractedLineItem, ExtractedReceipt


def _extraction():
    return ExtractedReceipt(
        store_name="Lidl", purchase_date="2026-08-10", total_amount=3.0,
        items=[ExtractedLineItem(description="Bread", amount=3.0, category="Продукты")],
    )


def _receipt_count():
    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        return db.query(Receipt).count()


def test_manifest_service_worker_and_icons_are_public(client):
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.json()["share_target"]["action"] == "/share"
    for icon in manifest.json()["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in sw.headers["content-type"]


def test_pages_link_the_manifest(logged_in_client):
    assert 'rel="manifest"' in logged_in_client.get("/").text


def test_several_photos_become_several_receipts(logged_in_client):
    with patch("app.receipts.extract_receipt_data", return_value=_extraction()):
        resp = logged_in_client.post(
            "/receipts",
            files=[
                ("file", ("a.jpg", io.BytesIO(b"a"), "image/jpeg")),
                ("file", ("b.jpg", io.BytesIO(b"b"), "image/jpeg")),
            ],
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert _receipt_count() == 2


def test_shared_photo_is_uploaded(logged_in_client):
    with patch("app.receipts.extract_receipt_data", return_value=_extraction()):
        resp = logged_in_client.post(
            "/share",
            files=[("file", ("s.jpg", io.BytesIO(b"s"), "image/jpeg"))],
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "#r-" in resp.headers["location"]
    assert _receipt_count() == 1


def test_share_without_image_goes_home(logged_in_client):
    resp = logged_in_client.post(
        "/share", files=[("file", ("n.txt", io.BytesIO(b"t"), "text/plain"))], follow_redirects=False
    )
    assert resp.headers["location"] == "/"
    assert _receipt_count() == 0
