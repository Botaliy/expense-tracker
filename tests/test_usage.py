from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image


def _response(tool_input, model="claude-haiku-5-5", **usage):
    usage = {"input_tokens": 1000, "output_tokens": 500, **usage}
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=tool_input)],
        model=model,
        usage=SimpleNamespace(**usage),
    )


@pytest.fixture
def api_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _calls():
    from app.database import SessionLocal
    from app.models import ApiCall

    with SessionLocal() as db:
        return db.query(ApiCall).order_by(ApiCall.id).all()


def test_cost_uses_list_prices_and_dated_model_ids():
    from app.usage import cost_usd, price_for

    # Haiku 4.5: $1 in / $5 out per million tokens.
    assert cost_usd("claude-haiku-4-5", 1000, 500) == pytest.approx(0.0035)
    # Cache writes 1.25× input, reads 0.1× input.
    assert cost_usd("claude-haiku-4-5", 0, 0, 1000, 1000) == pytest.approx(0.00135)
    assert price_for("claude-haiku-4-5-20251001") is price_for("claude-haiku-4-5")
    assert price_for("claude-haiku-4") is None
    # Haiku 5.5: $0.10 in / $0.50 out.
    assert cost_usd("claude-haiku-5-5", 1000, 500) == pytest.approx(0.00035)
    assert cost_usd("some-other-model", 1000, 500) is None


def test_each_kind_of_request_is_logged(logged_in_client, api_key, tmp_path):
    from app import ai_client

    image = tmp_path / "r.jpg"
    Image.new("RGB", (10, 10)).save(image)
    receipt = _response({"items": [{"description": "Milch", "amount": 1.1, "category": "Продукты", "product": "milk"}]})
    classify = _response({"category": "Транспорт", "product": "taxi"}, input_tokens=300, output_tokens=40)
    label = _response({"labels": [{"n": 1, "product": "beer"}]})

    for response, call in (
        (receipt, lambda: ai_client.extract_receipt_data(image, [], receipt_id=7)),
        (classify, lambda: ai_client.classify_expense("Такси", 12)),
        (label, lambda: ai_client.label_products([("BIER", None)])),
    ):
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
        with patch("app.ai_client.Anthropic", return_value=client):
            call()

    calls = _calls()
    assert [(c.purpose, c.receipt_id) for c in calls] == [("receipt", 7), ("classify", None), ("label", None)]
    assert calls[1].input_tokens == 300 and calls[1].output_tokens == 40
    assert calls[1].cost_usd == pytest.approx(300 * 0.10 / 1e6 + 40 * 0.50 / 1e6)


def test_logging_failure_never_breaks_the_request(logged_in_client, api_key):
    from app import ai_client

    broken = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"category": "Прочее", "product": "x"})])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: broken))  # no usage, no model
    with patch("app.ai_client.Anthropic", return_value=client):
        assert ai_client.classify_expense("что-то").category == "Прочее"
    assert _calls() == []


def test_usage_page_and_dashboard_link(logged_in_client):
    from app.database import SessionLocal
    from app.models import ApiCall

    with SessionLocal() as db:
        db.add_all([
            ApiCall(purpose="receipt", model="claude-haiku-4-5", input_tokens=2500, output_tokens=600, cost_usd=0.0055, receipt_id=3),
            ApiCall(purpose="receipt", model="claude-haiku-4-5", input_tokens=2400, output_tokens=500, cost_usd=0.0049),
            ApiCall(purpose="classify", model="claude-haiku-4-5", input_tokens=400, output_tokens=30, cost_usd=0.00055),
            ApiCall(purpose="receipt", model="claude-mystery-9", input_tokens=10, output_tokens=10, cost_usd=None),
        ])
        db.commit()

    page = logged_in_client.get("/usage").text
    assert "$0.0109" in page  # 0.0055 + 0.0049 + 0.00055 = 0.01095
    assert "Распознавание чеков" in page and "Подсказка при ручном вводе" in page
    assert "2,5\u00a0тыс → 600" in page  # tokens in the latest-calls table
    assert '<a href="/receipts/3">#3</a>' in page
    assert "Для 1 запроса модели нет в прайсе" in page

    dashboard = logged_in_client.get("/dashboard").text
    assert 'href="/usage"' in dashboard
    assert "4 запроса в этом месяце" in dashboard


def test_empty_usage_page(logged_in_client):
    assert "Запросов к Claude ещё не было" in logged_in_client.get("/usage").text


def test_usd_and_local_time(monkeypatch):
    from app.config import get_settings
    from app.templating import human_date_time, usd

    assert [usd(v) for v in (None, 0, 0.0031, 0.01095, 0.4, 0.42, 1234.5)] == [
        "—", "$0", "$0.0031", "$0.0109", "$0.40", "$0.42", "$1,234.50",
    ]

    monkeypatch.setenv("TIMEZONE", "Europe/Madrid")
    get_settings.cache_clear()
    try:
        # Stored as naive UTC by SQLite; Madrid is UTC+2 in summer.
        assert human_date_time(datetime(2026, 7, 1, 21, 30)).endswith(" 23:30")
        assert human_date_time(datetime(2026, 7, 1, 21, 30, tzinfo=UTC)).endswith(" 23:30")
    finally:
        get_settings.cache_clear()
