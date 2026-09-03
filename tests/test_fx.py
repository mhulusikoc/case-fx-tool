from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


NOT_IMPLEMENTED_RESPONSE = {
    "error": "not_implemented",
    "message": "Currency conversion is not implemented yet.",
}

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


def test_valid_convert_request_returns_not_implemented() -> None:
    response = client.get(
        "/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
    )

    assert response.status_code == 501
    assert response.json() == NOT_IMPLEMENTED_RESPONSE


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
    response = client.get(
        "/tools/convert?amount=250&from=eur&to=try&date=2026-08-28"
    )

    assert response.status_code == 501
    assert response.json() == NOT_IMPLEMENTED_RESPONSE


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
