import re
from datetime import date, timedelta

from app.forecast import merge_trips, predict_product

# Real purchase days from the production database, as of 2026-09-28.
TODAY = date(2026, 9, 28)


def _days(*isodates: str) -> list[date]:
    return [date.fromisoformat(d) for d in isodates]


def test_merge_trips_collapses_consecutive_days_to_the_latest():
    days = _days("2026-08-26", "2026-08-25", "2026-09-02", "2026-09-02", "2026-09-12")
    assert merge_trips(days) == _days("2026-08-26", "2026-09-02", "2026-09-12")


def test_regular_product_is_due_one_median_gap_after_the_last_purchase():
    grapes = _days("2026-09-06", "2026-09-12", "2026-09-19")
    p = predict_product("grapes", "Продукты", grapes, TODAY)
    assert p is not None
    assert p.interval_days == 6.5
    assert p.due_date == date(2026, 9, 25)
    assert p.days_until == -3
    assert p.status == "due"


def test_upcoming_product_counts_days_ahead():
    chicken = _days("2026-08-20", "2026-08-26", "2026-09-08", "2026-09-22")
    p = predict_product("chicken", "Продукты", chicken, TODAY)
    assert p is not None
    assert p.interval_days == 13
    assert p.days_until == 7
    assert p.status == "soon"


def test_one_shopping_spree_is_not_a_habit():
    # Three days in a row is one trip, not three purchases a day apart.
    iced_tea = _days("2026-08-25", "2026-08-26", "2026-08-27")
    assert predict_product("iced tea", "Продукты", iced_tea, TODAY) is None


def test_long_unbought_product_is_dropped():
    # Every ~3 days, last bought 20 days ago.
    salami = _days("2026-08-25", "2026-09-02", "2026-09-05", "2026-09-08")
    assert predict_product("salami", "Продукты", salami, TODAY) is None


def test_irregular_product_is_dropped():
    tobacco = _days("2026-08-10", "2026-08-13", "2026-08-21", "2026-09-04")  # gaps 3, 8, 14
    assert predict_product("tobacco sticks", "Алкоголь/табак", tobacco, date(2026, 9, 6)) is None


def _buy(client, product, category, day, description=None):
    client.post(
        "/expenses",
        data={
            "description": description or product,
            "amount": "5",
            "category": category,
            "product": product,
            "purchase_date": day.isoformat(),
        },
    )


def _weekly(client, product, category, description=None):
    """Bought every 7 days, last time 6 days ago: due tomorrow."""
    today = date.today()
    for ago in (20, 13, 6):
        _buy(client, product, category, today - timedelta(days=ago), description)


def test_dashboard_shows_what_is_due_soon(logged_in_client):
    _weekly(logged_in_client, "cat food", "Кошечка")

    page = logged_in_client.get("/dashboard").text
    assert "Скоро понадобится" in page
    assert "cat food" in page
    assert "завтра" in page
    assert "раз в ~7 дней" in page

    # Not in a past month's view: the forecast is about today.
    past = logged_in_client.get("/dashboard", params={"month": "2020-01"}).text
    assert "Скоро понадобится" not in past


def test_hiding_a_product_from_the_dashboard(logged_in_client):
    _weekly(logged_in_client, "cat food", "Кошечка")
    _weekly(logged_in_client, "plastic bag", "Прочее")

    resp = logged_in_client.post(
        "/forecast/exclusions",
        data={"product": "Plastic Bag ", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"

    page = logged_in_client.get("/dashboard").text
    assert "cat food" in page
    assert '<a class="t" href="/search?q=plastic%20bag">' not in page

    # The forecast page lists it as excluded and can bring it back.
    forecast = logged_in_client.get("/forecast").text
    assert 'title="Снова учитывать plastic bag"' in forecast
    logged_in_client.post("/forecast/exclusions/delete", data={"product": "plastic bag"})
    assert '<a class="t" href="/search?q=plastic%20bag">' in logged_in_client.get("/dashboard").text


def test_excluded_category_is_ignored(logged_in_client):
    _weekly(logged_in_client, "coffee to go", "Кафе/рестораны")
    assert "coffee to go" in logged_in_client.get("/forecast").text

    logged_in_client.post("/forecast/categories", data={"excluded": ["Кафе/рестораны", "Нет такой"]})
    page = logged_in_client.get("/forecast").text
    assert '<a class="t" href="/search?q=coffee%20to%20go">' not in page
    assert re.search(r'value="Кафе/рестораны"[^>]*checked', page)
    assert not re.search(r'value="Нет такой"', page)

    # Unticking everything sends no field at all.
    logged_in_client.post("/forecast/categories", data={})
    assert '<a class="t" href="/search?q=coffee%20to%20go">' in logged_in_client.get("/forecast").text


def test_redirect_stays_on_site(logged_in_client):
    resp = logged_in_client.post(
        "/forecast/exclusions",
        data={"product": "beer", "next": "//evil.example"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/forecast"


def test_defaults_are_seeded_once(client):  # noqa: ARG001 — sets up the test database
    import app.database as db_module
    from app.forecast import CATEGORY, PRODUCT, exclusions, remove_exclusion
    from app.models import ForecastExclusion

    ForecastExclusion.metadata.tables["forecast_exclusions"].drop(db_module.engine)
    db_module.init_db()
    with db_module.SessionLocal() as db:
        excluded = exclusions(db)
        assert "Кафе/рестораны" in excluded[CATEGORY]
        assert excluded[PRODUCT] == {"plastic bag"}
        remove_exclusion(db, PRODUCT, "plastic bag")

    # A restart must not bring back what the user removed.
    db_module.init_db()
    with db_module.SessionLocal() as db:
        assert exclusions(db)[PRODUCT] == set()
