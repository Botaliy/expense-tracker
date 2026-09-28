import io
import re
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

from app.ai_client import ExpenseClassification
from app.schemas import ExtractedLineItem, ExtractedReceipt


def _receipt_id(resp) -> int:
    return int(re.search(r"#r-(\d+)$", resp.headers["location"]).group(1))


def _fake_client(tool_input: dict):
    block = SimpleNamespace(type="tool_use", input=tool_input)
    response = SimpleNamespace(
        content=[block],
        model="claude-haiku-4-5",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))


def test_normalize_product():
    from app.products import normalize_product

    assert normalize_product("  Coffee   Beans. ") == "coffee beans"
    assert normalize_product("«Cat food»") == "cat food"
    assert normalize_product("") is None
    assert normalize_product("...") is None
    assert normalize_product(None) is None


def test_missing_column_is_added_to_existing_database(tmp_path, monkeypatch):
    import app.database as db_module

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        # line_items as it was before products existed
        conn.execute(text(
            "CREATE TABLE line_items (id INTEGER PRIMARY KEY, receipt_id INTEGER, "
            "description VARCHAR(255), amount FLOAT, quantity FLOAT, category VARCHAR(64))"
        ))
        conn.execute(text(
            "INSERT INTO line_items (receipt_id, description, amount, category) "
            "VALUES (1, 'Milch', 1.1, 'Продукты')"
        ))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    columns = {c["name"] for c in inspect(engine).get_columns("line_items")}
    assert "product" in columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT description, product FROM line_items")).one() == ("Milch", None)


def test_receipt_recognition_stores_products_and_sends_vocabulary(logged_in_client):
    extraction = ExtractedReceipt(
        store_name="Lidl",
        purchase_date=date.today().isoformat(),
        total_amount=15.0,
        items=[
            ExtractedLineItem(description="KAFFEE CREMA 1KG", amount=12.99, category="Продукты", product="Coffee Beans"),
            ExtractedLineItem(description="MILCH 3,5%", amount=2.01, category="Продукты", product="milk"),
        ],
    )
    with patch("app.receipts.extract_receipt_data", return_value=extraction) as extract:
        logged_in_client.post("/receipts", files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")})
        # The second receipt sees the first one's products as the vocabulary.
        logged_in_client.post("/receipts", files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")})

    assert extract.call_args_list[0].args[1] == []
    assert set(extract.call_args_list[1].args[1]) == {"coffee beans", "milk"}

    from app.database import SessionLocal
    from app.models import LineItem

    with SessionLocal() as db:
        assert db.query(LineItem).filter_by(description="KAFFEE CREMA 1KG").first().product == "coffee beans"


def test_manual_expense_uses_product_from_form_without_model_call(logged_in_client):
    with patch("app.routers.receipts.classify_expense") as classify:
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Кофе с собой", "amount": "3,20", "category": "Кафе/рестораны", "product": "coffee to go"},
            follow_redirects=False,
        )
    classify.assert_not_called()

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        assert db.get(Receipt, _receipt_id(resp)).items[0].product == "coffee to go"


def test_manual_expense_asks_model_for_missing_product(logged_in_client):
    with patch(
        "app.routers.receipts.classify_expense",
        return_value=ExpenseClassification("Транспорт", "taxi"),
    ) as classify:
        resp = logged_in_client.post(
            "/expenses",
            data={"description": "Такси домой", "amount": "18", "category": "Кафе/рестораны"},
            follow_redirects=False,
        )
    classify.assert_called_once()

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        item = db.get(Receipt, _receipt_id(resp)).items[0]
        assert item.product == "taxi"
        assert item.category == "Кафе/рестораны"  # the user's own pick wins


def test_product_can_be_edited_and_cleared(logged_in_client):
    resp = logged_in_client.post(
        "/expenses",
        data={"description": "Корм", "amount": "20", "category": "Кошечка", "product": "cat fod"},
        follow_redirects=False,
    )
    receipt_id = _receipt_id(resp)

    from app.database import SessionLocal
    from app.models import Receipt

    with SessionLocal() as db:
        item_id = db.get(Receipt, receipt_id).items[0].id

    detail = logged_in_client.get(f"/receipts/{receipt_id}").text
    assert 'value="cat fod"' in detail
    assert '<option value="cat fod"></option>' in detail  # vocabulary datalist

    logged_in_client.post(
        f"/receipts/{receipt_id}/items/{item_id}",
        data={"category": "Кошечка", "product": "Cat Food"},
    )
    with SessionLocal() as db:
        assert db.get(Receipt, receipt_id).items[0].product == "cat food"

    logged_in_client.post(f"/receipts/{receipt_id}/items/{item_id}", data={"category": "Кошечка", "product": " "})
    with SessionLocal() as db:
        assert db.get(Receipt, receipt_id).items[0].product is None


def test_search_by_product_across_languages(logged_in_client):
    for description, product, store in (
        ("KAFFEE CREMA 1KG", "coffee beans", "Lidl"),
        ("CAFE SOLO", "coffee to go", "Bar Pepe"),
        ("Капучино", "coffee to go", ""),
        ("MILCH", "milk", "Lidl"),
    ):
        logged_in_client.post(
            "/expenses",
            data={"description": description, "amount": "3", "category": "Продукты", "product": product, "store_name": store},
        )

    page = logged_in_client.get("/search", params={"q": "coffee"}).text
    for description in ("KAFFEE CREMA 1KG", "CAFE SOLO", "Капучино"):
        assert description in page
    assert "MILCH" not in page
    assert "Что вошло" in page
    assert "coffee to go <b>6" in page  # two purchases grouped under one product

    suggestions = logged_in_client.get("/search/suggest", params={"q": "cof"}).text
    assert '<span class="name">coffee to go</span>' in suggestions
    assert "KAFFEE" not in suggestions  # products are suggested, not raw receipt text

    often = logged_in_client.get("/search").text
    assert "coffee to go<span>2×</span>" in often


def test_classify_expense_parses_category_and_product(monkeypatch):
    from app import ai_client
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    with patch("app.ai_client.Anthropic", return_value=_fake_client({"category": "Кошечка", "product": " Cat Litter "})):
        result = ai_client.classify_expense("Наполнитель", 9.5, ["cat food"])
    get_settings.cache_clear()

    assert result == ExpenseClassification("Кошечка", "cat litter")


def test_label_products_keeps_order_and_skips_missing(monkeypatch):
    from app import ai_client
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    reply = {"labels": [{"n": 3, "product": "Beer"}, {"n": 1, "product": "coffee beans"}, {"n": "x"}]}
    with patch("app.ai_client.Anthropic", return_value=_fake_client(reply)):
        labels = ai_client.label_products([("KAFFEE", "Lidl"), ("???", None), ("BIER", None)])
    get_settings.cache_clear()

    assert labels == ["coffee beans", None, "beer"]
