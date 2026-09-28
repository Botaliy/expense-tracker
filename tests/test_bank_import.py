import io
from datetime import date
from unittest.mock import patch

from app import bank_import
from app.ai_client import StatementLabel
from app.schemas import ExtractedLineItem, ExtractedReceipt

REVOLUT_CSV = """Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance
CARD_PAYMENT,Current,2026-08-10 12:01:00,2026-08-11 09:00:00,Alphamega,-23.40,0.00,EUR,COMPLETED,100.00
CARD_PAYMENT,Current,2026-08-12 08:00:00,2026-08-12 09:00:00,Wolt,-15.00,0.00,EUR,COMPLETED,85.00
CARD_PAYMENT,Current,2026-08-12 08:30:00,,Bolt,-7.10,0.00,EUR,PENDING,
TOPUP,Current,2026-08-01 10:00:00,2026-08-01 10:00:00,Top-Up by *1234,500.00,0.00,EUR,COMPLETED,500.00
TRANSFER,Current,2026-08-05 10:00:00,2026-08-05 10:00:00,To Landlord,-900.00,0.00,EUR,COMPLETED,0.00
CARD_PAYMENT,Current,2026-08-06 10:00:00,,Declined shop,-5.00,0.00,EUR,DECLINED,
EXCHANGE,Current,2026-08-07 10:00:00,2026-08-07 10:00:00,Exchanged to USD,-50.00,0.00,EUR,COMPLETED,0.00
"""

BOC_CSV = """Account;357-01-123456-01
Statement period;01/08/2026 - 31/08/2026

Transaction Date;Value Date;Description;Debit;Credit;Balance
10/08/2026;11/08/2026;POS PURCHASE ALPHAMEGA NICOSIA;23,40;;1.200,00
13/08/2026;13/08/2026;SALARY ACME LTD;;2.500,00;3.700,00
14/08/2026;14/08/2026;ATM WITHDRAWAL;100,00;;3.600,00
15/08/2026;15/08/2026;POS PURCHASE CYTA;35,99;;3.564,01
"""


def test_revolut_keeps_only_money_spent():
    bank, rows = bank_import.parse_statement(REVOLUT_CSV.encode())
    assert bank == "revolut"
    assert [(r.description, r.amount, r.kind) for r in rows] == [
        ("Alphamega", 23.40, "card"),
        ("Wolt", 15.00, "card"),
        ("Bolt", 7.10, "card"),
        ("To Landlord", 900.00, "transfer"),
    ]
    assert rows[0].booked_on == date(2026, 8, 10)


def test_other_bank_columns_found_by_name():
    bank, rows = bank_import.parse_statement(BOC_CSV.encode("cp1253"))
    assert bank == "bank"
    assert [(r.booked_on, r.description, r.amount) for r in rows] == [
        (date(2026, 8, 10), "POS PURCHASE ALPHAMEGA NICOSIA", 23.40),
        (date(2026, 8, 15), "POS PURCHASE CYTA", 35.99),
    ]


def test_unknown_layout_falls_back_to_model():
    csv_text = "Když;Co;Kolik\n10.08.2026;Shop;-12,00\n"
    calls = []

    def fake(rows):
        calls.append(rows)
        return 0, bank_import.ColumnMap(date=0, description=1, amount=2)

    _, rows = bank_import.parse_statement(csv_text.encode(), map_columns=fake)
    assert calls and rows[0].amount == 12.0


def test_parse_number_formats():
    assert bank_import.parse_number("1.234,56") == 1234.56
    assert bank_import.parse_number("1,234.56") == 1234.56
    assert bank_import.parse_number("-12,5") == -12.5
    assert bank_import.parse_number("€ 7") == 7.0
    assert bank_import.parse_number("12.00 DR") == -12.0
    assert bank_import.parse_number("") is None


def _labels(descriptions):
    return [StatementLabel(category="Кафе/рестораны", product="food delivery", store=d.title()) for d in descriptions]


def _upload(client, text=REVOLUT_CSV):
    return client.post(
        "/import", files={"file": ("s.csv", io.BytesIO(text.encode()), "text/csv")}, follow_redirects=False
    )


def _db():
    from app.database import SessionLocal

    return SessionLocal()


def test_import_matches_existing_receipt_instead_of_duplicating(logged_in_client):
    extraction = ExtractedReceipt(
        store_name="Alphamega", purchase_date="2026-08-09", total_amount=23.40,
        items=[ExtractedLineItem(description="Milk", amount=23.40, category="Продукты")],
    )
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        logged_in_client.post("/receipts", files={"file": ("r.jpg", io.BytesIO(b"x"), "image/jpeg")})

    resp = _upload(logged_in_client)
    assert "matched=1" in resp.headers["location"]
    page = logged_in_client.get("/import").text
    assert "Уже есть чек" in page and "To Landlord" in page

    from app.models import BankTransaction, Receipt

    with _db() as db:
        ids = {t.description: t.id for t in db.query(BankTransaction)}
    with patch("app.routers.bank.classify_statement", side_effect=lambda d, *a: _labels(d)) as model:
        logged_in_client.post(
            "/import/confirm",
            data={"include": [ids["Alphamega"], ids["Wolt"], ids["Bolt"]], "keep": [ids["Alphamega"]]},
        )
    assert model.call_args.args[0] == ["Wolt", "Bolt"]

    with _db() as db:
        receipts = db.query(Receipt).all()
        assert len(receipts) == 3  # the photo + Wolt + Bolt; Alphamega linked, landlord skipped
        wolt = next(r for r in receipts if r.source == "bank" and r.total_amount == 15.0)
        assert wolt.items[0].category == "Кафе/рестораны"
        statuses = {t.description: t.status for t in db.query(BankTransaction)}
    assert statuses == {"Alphamega": "matched", "Wolt": "created", "Bolt": "created", "To Landlord": "skipped"}

    # The same statement again adds nothing.
    again = _upload(logged_in_client)
    assert "added=0" in again.headers["location"] and "seen=4" in again.headers["location"]


def test_photo_after_import_replaces_bank_entry(logged_in_client):
    _upload(logged_in_client)
    from app.models import BankTransaction, Receipt

    with _db() as db:
        wolt_id = db.query(BankTransaction).filter_by(description="Wolt").one().id
    with patch("app.routers.bank.classify_statement", side_effect=lambda d, *a: _labels(d)):
        logged_in_client.post("/import/confirm", data={"include": [wolt_id]})

    extraction = ExtractedReceipt(
        store_name="Wolt", purchase_date="2026-08-12", total_amount=15.0,
        items=[ExtractedLineItem(description="Pizza", amount=15.0, category="Кафе/рестораны")],
    )
    with patch("app.receipts.extract_receipt_data", return_value=extraction):
        logged_in_client.post("/receipts", files={"file": ("r.jpg", io.BytesIO(b"w"), "image/jpeg")})

    with _db() as db:
        receipts = db.query(Receipt).all()
        assert [r.source for r in receipts] == [None]
        txn = db.get(BankTransaction, wolt_id)
        assert (txn.receipt_id, txn.status) == (receipts[0].id, "matched")


def test_discard_drops_preview(logged_in_client):
    _upload(logged_in_client)
    logged_in_client.post("/import/discard")
    assert "Загрузить" in logged_in_client.get("/import").text
    assert "added=4" in _upload(logged_in_client).headers["location"]


def test_bad_file_shows_error(logged_in_client):
    resp = _upload(logged_in_client, "hello world\nfoo bar\n")
    assert "error=" in resp.headers["location"]
    assert "Не нашёл" in logged_in_client.get(resp.headers["location"]).text
