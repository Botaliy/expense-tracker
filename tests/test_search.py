from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest


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


@pytest.fixture
def expand():
    """Never reach the real API from tests: `.env` may hold a real key."""
    with patch("app.routers.search.expand_search_terms") as mock:
        yield mock


def test_empty_search_shows_examples(logged_in_client, expand):
    page = logged_in_client.get("/search").text
    assert "Кофе, пиво, Lidl" in page
    assert 'href="/search?q=%D0%BA%D0%BE%D1%84%D0%B5"' in page  # «кофе»
    expand.assert_not_called()


def test_search_matches_translations_case_insensitively(logged_in_client, expand):
    expand.return_value = ["кофе", "kaffee", "café"]
    _add(logged_in_client, "KAFFEE CREMA 1KG", "12,99", store="Lidl")
    _add(logged_in_client, "CAFÉ SOLO", "1,80", store="Bar Pepe")
    _add(logged_in_client, "Milch", "1,10", store="Lidl")

    page = logged_in_client.get("/search", params={"q": "Кофе"}).text

    assert "KAFFEE CREMA 1KG" in page
    assert "CAFÉ SOLO" in page  # É only folds in Python, not in SQLite
    assert "Milch" not in page
    assert "14<span class=\"cents\">,79</span>" in page  # 12.99 + 1.80
    assert "по «kaffee»" in page
    assert '<span class="term">café</span>' in page


def test_search_matches_store_name(logged_in_client, expand):
    expand.return_value = ["lidl"]
    _add(logged_in_client, "Milch", "1,10", store="LIDL")
    _add(logged_in_client, "Brot", "2,00", store="Lidl")
    _add(logged_in_client, "Brot", "3,00", store="Aldi")

    page = logged_in_client.get("/search", params={"q": "Lidl"}).text
    assert "3,10" in page.replace('<span class="cents">', "").replace("</span>", "")
    assert "2 раза" in page  # one place despite different spelling


def test_exact_search_skips_translation(logged_in_client, expand):
    _add(logged_in_client, "Kaffee", "5")
    page = logged_in_client.get("/search", params={"q": "кофе", "exact": "1"}).text
    expand.assert_not_called()
    assert "ничего не нашлось" in page
    assert "поиск с переводом" in page


def test_translation_failure_falls_back_to_plain_search(logged_in_client, expand):
    from app.ai_client import SearchExpansionError

    expand.side_effect = SearchExpansionError("no key")
    _add(logged_in_client, "кофе в зёрнах", "9")
    page = logged_in_client.get("/search", params={"q": "кофе"}).text
    assert "Перевод сейчас недоступен" in page
    assert "кофе в зёрнах" in page


def test_search_ignores_items_older_than_a_year(logged_in_client, expand):
    expand.return_value = ["пиво"]
    _add(logged_in_client, "Пиво старое", "3", day=date.today().replace(year=date.today().year - 2))
    _add(logged_in_client, "Пиво свежее", "4")
    page = logged_in_client.get("/search", params={"q": "пиво"}).text
    assert "Пиво свежее" in page
    assert "Пиво старое" not in page


def test_monthly_average_starts_at_first_purchase():
    from app.search import Match, summarize

    months = [(2026, m) for m in range(1, 13)]
    matches = [
        Match(1, "a", 30.0, None, "Прочее", 1, None, None, date(2026, 10, 1), "a"),
        Match(2, "a", 60.0, None, "Прочее", 2, None, None, date(2026, 12, 1), "a"),
    ]
    result = summarize(matches, months)
    assert result["total"] == 90
    assert result["per_month"] == 30  # Oct–Dec, not all twelve months
    assert result["average_price"] == 45


def test_expand_search_terms_parses_and_filters(monkeypatch):
    from app import ai_client
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    ai_client._expand_cached.cache_clear()

    block = SimpleNamespace(type="tool_use", input={"terms": ["Kaffee", "ca", "café", "кофе", 5]})
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(content=[block]))
    )
    with patch("app.ai_client.Anthropic", return_value=fake_client):
        terms = ai_client.expand_search_terms("  Кофе ")

    # Query first, lowercased, too-short and non-string terms dropped, no duplicates.
    assert terms == ["кофе", "kaffee", "café"]
    ai_client._expand_cached.cache_clear()
    get_settings.cache_clear()
