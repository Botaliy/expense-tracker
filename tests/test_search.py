from datetime import date


def _add(client, description, amount, store="", day=None, category="Продукты"):
    client.post(
        "/expenses",
        data={
            "description": description,
            "amount": amount,
            "category": category,
            "purchase_date": (day or date.today()).isoformat(),
            "store_name": store,
        },
    )


def test_empty_search_shows_often_bought(logged_in_client):
    _add(logged_in_client, "Kaffee Crema", "12,99")
    _add(logged_in_client, "Kaffee Crema", "12,99")
    _add(logged_in_client, "KAFFEE  CREMA", "12,99")
    _add(logged_in_client, "Milch", "1,10")

    page = logged_in_client.get("/search").text
    assert "Часто покупаешь" in page
    # Spellings of one product are one entry, shown the most common way, listed first.
    assert page.index("Kaffee Crema<span>3×</span>") < page.index("Milch<span>1×</span>")


def _results(page):
    """The page without the dropdown, which lists every name regardless of the query."""
    before, _, rest = page.partition('<template id="all-names">')
    return before + rest.partition("</template>")[2]


def test_search_is_case_and_accent_insensitive(logged_in_client):
    _add(logged_in_client, "KAFFEE CREMA 1KG", "12,99", store="Lidl")
    _add(logged_in_client, "CAFÉ SOLO", "1,80", store="Bar Pepe")
    _add(logged_in_client, "Milch", "1,10", store="Lidl")

    page = _results(logged_in_client.get("/search", params={"q": "kaffee"}).text)
    assert "KAFFEE CREMA 1KG" in page
    assert "CAFÉ SOLO" not in page
    assert "Milch" not in page

    page = logged_in_client.get("/search", params={"q": "café"}).text
    assert "CAFÉ SOLO" in page  # É only folds in Python, not in SQLite


def test_search_matches_store_name(logged_in_client):
    _add(logged_in_client, "Milch", "1,10", store="LIDL")
    _add(logged_in_client, "Brot", "2,00", store="Lidl")
    _add(logged_in_client, "Brot", "3,00", store="Aldi")

    page = logged_in_client.get("/search", params={"q": "Lidl"}).text
    assert "3,10" in page.replace('<span class="cents">', "").replace("</span>", "")
    assert "2 раза" in page  # one place despite different spelling


def test_nothing_found(logged_in_client):
    _add(logged_in_client, "Kaffee", "5")
    page = logged_in_client.get("/search", params={"q": "кофе"}).text
    assert "ничего не нашлось по «кофе»" in page


def _dropdown(page):
    return page.split('<template id="all-names">')[1].split("</template>")[0]


def test_dropdown_lists_every_recorded_name(logged_in_client):
    _add(logged_in_client, "Kaffee Crema", "12,99", store="Lidl")
    _add(logged_in_client, "Kaffee Crema", "12,99", store="Lidl")
    _add(logged_in_client, "KAFFEE  CREMA", "12,99", store="Lidl")
    _add(logged_in_client, "Milch", "1,10", store="Kafe Mokka")

    html = _dropdown(logged_in_client.get("/search").text)
    names = [line.strip() for line in html.split("\n") if 'class="name"' in line]
    # Most bought first; one entry per product despite spelling; stores too.
    assert names == [
        '<span class="name">Kaffee Crema</span>',
        '<span class="name">Lidl</span>',
        '<span class="name">Milch</span>',
        '<span class="name">Kafe Mokka</span>',
    ]
    assert 'data-key="kaffee crema" data-kind="product"' in html
    assert 'data-key="lidl" data-kind="store"' in html
    assert "3 раза" in html  # Kaffee Crema bought three times

    # The list is there on the results page too, for refining the query.
    assert "Kafe Mokka" in _dropdown(logged_in_client.get("/search", params={"q": "milch"}).text)


def test_store_suggestion_counts_visits_not_items(logged_in_client):
    from app.database import SessionLocal
    from app.models import LineItem, Receipt, ReceiptStatus

    with SessionLocal() as db:
        receipt = Receipt(store_name="Lidl", purchase_date=date.today(), status=ReceiptStatus.PROCESSED)
        for name in ("Milch", "Brot", "Käse"):
            receipt.items.append(LineItem(description=name, amount=1.0, category="Продукты"))
        db.add(receipt)
        db.commit()

    html = _dropdown(logged_in_client.get("/search").text)
    store = html[html.index('data-kind="store"'):]
    assert "1 раз ·" in store


def test_search_ignores_items_older_than_a_year(logged_in_client):
    _add(logged_in_client, "Пиво старое", "3", day=date.today().replace(year=date.today().year - 2))
    _add(logged_in_client, "Пиво свежее", "4")
    page = _results(logged_in_client.get("/search", params={"q": "пиво"}).text)
    assert "Пиво свежее" in page
    assert "Пиво старое" not in page


def test_monthly_average_starts_at_first_purchase():
    from app.search import Match, summarize

    months = [(2026, m) for m in range(1, 13)]
    matches = [
        Match(1, "a", None, 30.0, None, "Прочее", 1, None, None, date(2026, 10, 1)),
        Match(2, "a", None, 60.0, None, "Прочее", 2, None, None, date(2026, 12, 1)),
    ]
    result = summarize(matches, months)
    assert result["total"] == 90
    assert result["per_month"] == 30  # Oct–Dec, not all twelve months
    assert result["average_price"] == 45
