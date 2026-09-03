import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from fastapi.testclient import TestClient

from app.main import app
from app import fx_service
from app.fx_service import fetch_rate


client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared expected error bodies
# ---------------------------------------------------------------------------

INVALID_AMOUNT_RESPONSE = {
    "error": "invalid_amount",
    "message": "Amount must be greater than zero and have at most two decimal places.",
}

INVALID_CURRENCY_RESPONSE = {
    "error": "invalid_currency",
    "message": "Currency codes must be three-letter ISO-style codes.",
}

SAME_CURRENCY_RESPONSE = {
    "error": "same_currency",
    "message": "Source and target currencies must be different.",
}

FUTURE_DATE_RESPONSE = {
    "error": "future_date",
    "message": "Exchange rates are not available for future dates.",
}

INVALID_REQUEST_RESPONSE = {
    "error": "invalid_request",
    "message": "The request parameters are invalid.",
}


def _mock_fetch_rate(rate: Decimal, rate_date: str):
    """Return a context manager that patches fetch_rate with a fixed return value."""
    return patch(
        "app.main.fetch_rate",
        new=AsyncMock(return_value=(rate, rate_date)),
    )


def test_valid_convert_request_returns_200() -> None:
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 250
    assert body["from"] == "EUR"
    assert body["to"] == "TRY"
    assert Decimal(str(body["rate"])) == Decimal("47.1234")
    assert Decimal(str(body["result"])) == Decimal("11780.85")
    assert body["rate_date"] == "2026-08-28"
    assert body["asked_date"] == "2026-08-28"
    assert body["source"] == "ECB via frankfurter.dev"


def test_zero_amount_returns_invalid_amount() -> None:
    response = client.get("/tools/convert?amount=0&from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_negative_amount_returns_invalid_amount() -> None:
    response = client.get("/tools/convert?amount=-10&from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_amount_with_more_than_two_decimal_places_returns_invalid_amount() -> None:
    response = client.get(
        "/tools/convert?amount=1.1234567890&from=EUR&to=TRY&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == INVALID_AMOUNT_RESPONSE


def test_malformed_currency_returns_invalid_currency() -> None:
    response = client.get(
        "/tools/convert?amount=250&from=EURO&to=TRY&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == INVALID_CURRENCY_RESPONSE


def test_lowercase_currency_codes_pass_validation() -> None:
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=eur&to=try&date=2026-08-28"
        )

    assert response.status_code == 200


def test_same_currency_returns_same_currency_error() -> None:
    response = client.get(
        "/tools/convert?amount=250&from=eur&to=EUR&date=2026-08-28"
    )

    assert response.status_code == 400
    assert response.json() == SAME_CURRENCY_RESPONSE


def test_future_date_returns_future_date_error() -> None:
    future_date = date.today() + timedelta(days=1)

    response = client.get(
        f"/tools/convert?amount=250&from=EUR&to=TRY&date={future_date}"
    )

    assert response.status_code == 400
    assert response.json() == FUTURE_DATE_RESPONSE


def test_missing_amount_returns_invalid_request() -> None:
    response = client.get("/tools/convert?from=EUR&to=TRY&date=2026-08-28")

    assert response.status_code == 400
    assert response.json() == INVALID_REQUEST_RESPONSE


def test_invalid_date_format_returns_invalid_request() -> None:
    response = client.get("/tools/convert?amount=250&from=EUR&to=TRY&date=2026/08/28")

    assert response.status_code == 400
    assert response.json() == INVALID_REQUEST_RESPONSE


# ---------------------------------------------------------------------------
# Upstream integration tests (network-free)
# ---------------------------------------------------------------------------


def test_upstream_rate_date_differs_from_asked_date() -> None:
    """When upstream returns a different date (e.g. weekend), asked_date and
    rate_date must both be preserved independently."""
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-30"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asked_date"] == "2026-08-30"
    assert body["rate_date"] == "2026-08-28"


def test_result_uses_full_upstream_precision() -> None:
    """result must be computed as amount * rate (full precision), then rounded
    to 2 decimal places — NOT rate-rounded-first."""
    # 250 * 47.1234 = 11780.85 exactly at 2dp
    with _mock_fetch_rate(Decimal("47.1234"), "2026-08-28"):
        response = client.get(
            "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
        )

    assert response.status_code == 200
    assert Decimal(str(response.json()["result"])) == Decimal("11780.85")


# ---------------------------------------------------------------------------
# fetch_rate unit tests — real function, no mock.patch, no network
# ---------------------------------------------------------------------------

_FAKE_UPSTREAM_BODY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-08-28",
    "rates": {"TRY": 47.1234},
}


class _FakeFrankfurterTransport(httpx.AsyncBaseTransport):
    """Intercepts every request and returns a fixed JSON payload.

    Records the last request so tests can assert on path and query params.
    """

    def __init__(self, body: dict, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(
            status_code=self.status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self.body).encode(),
            request=request,
        )


@pytest.mark.anyio
async def test_fetch_rate_request_path_and_params() -> None:
    """fetch_rate must call /v1/<date> with base=FROM and symbols=TO."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=fx_service.FX_UPSTREAM_BASE,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    assert transport.last_request is not None
    assert transport.last_request.url.path == "/v1/2026-08-28"
    assert transport.last_request.url.params["base"] == "EUR"
    assert transport.last_request.url.params["symbols"] == "TRY"


@pytest.mark.anyio
async def test_fetch_rate_returns_decimal_and_date() -> None:
    """fetch_rate must parse the upstream JSON into Decimal rate and string date."""
    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=fx_service.FX_UPSTREAM_BASE,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"


@pytest.mark.anyio
async def test_fetch_rate_uses_fx_upstream_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """FX_UPSTREAM_BASE env var must control the base URL used by fetch_rate."""
    custom_base = "https://custom.fx.example.com"
    monkeypatch.setattr(fx_service, "FX_UPSTREAM_BASE", custom_base)

    transport = _FakeFrankfurterTransport(_FAKE_UPSTREAM_BODY)
    mock_client = httpx.AsyncClient(
        base_url=custom_base,
        transport=transport,
    )

    rate, rate_date = await fetch_rate(
        "EUR", "TRY", date(2026, 8, 28), _client=mock_client
    )

    # Verify the URL built inside fetch_rate used the patched base
    assert transport.last_request is not None
    assert custom_base in str(transport.last_request.url)
    assert rate == Decimal("47.1234")
    assert rate_date == "2026-08-28"
