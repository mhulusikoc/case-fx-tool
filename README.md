# FX Conversion Tool

A small FastAPI service that fetches historical exchange rates from the European Central Bank (via [Frankfurter](https://frankfurter.dev)) and converts a currency amount for a given date.

---

## Requirements & Setup

- Python 3.11+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Running

```bash
./run.sh
```

Override the port (default `8080`):

```bash
PORT=8091 ./run.sh
```

All upstream requests use `FX_UPSTREAM_BASE`; when unset, it defaults to `https://api.frankfurter.dev`.

Once running:

| | |
|---|---|
| Service | http://localhost:8080 |
| API docs | http://localhost:8080/docs |
| Health check | http://localhost:8080/health |

---

## Testing

```bash
./test.sh
```

Tests use fake httpx transports and make **no real network calls**.  
They pass even when the upstream is unreachable:

```bash
FX_UPSTREAM_BASE=http://127.0.0.1:9 ./test.sh
```

---

## Endpoint

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

### Successful response — `200 OK`

```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.1234,
  "result": 11780.85,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

`result` is computed as `amount × rate` at full upstream precision, then rounded to 2 decimal places.  
`rate_date` is the date the rate **actually belongs to** (may differ from `asked_date`).

---

## Edge-Case Behaviour

| Scenario | Behaviour |
|---|---|
| **Weekend / public holiday** | Frankfurter returns the most recently published ECB rate. The response exposes both `asked_date` (what was requested) and `rate_date` (which trading day the rate belongs to) so the caller can always tell the customer exactly which day's number they are seeing. |
| **Future date** | `400 future_date` — rejected immediately, no upstream call. |
| **Date before 1999-01-04** | `400 date_out_of_range` — ECB series does not cover it. |
| **Invalid currency format** | `400 invalid_currency` — must be a 3-letter alphabetic code. |
| **Unsupported currency** | `400 unsupported_currency` — format is valid but Frankfurter does not recognise the symbol. |
| **Same currency for from and to** | `400 same_currency`. |
| **Amount missing / zero / negative / >2 decimals** | `400 invalid_amount` or `400 invalid_request`. |
| **Upstream timeout** | `504 upstream_timeout` — the service fails safely; no invented rate is returned. |
| **Upstream network error or HTTP 5xx** | `502 upstream_error`. |
| **Upstream returns non-JSON or malformed payload** | `502 invalid_upstream_response` — only a validated, positive, finite rate is ever used. |
| **Rate cache** | A successful rate lookup for `(from, to, asked_date)` is cached in memory. Repeat requests skip the upstream call. `amount` is not part of the cache key. Errors are never cached — a transient failure does not block future retries. |

---

## Error Codes

| HTTP | `error` | When |
|---|---|---|
| 400 | `invalid_request` | Missing or unparseable query parameter |
| 400 | `invalid_amount` | Amount ≤ 0 or more than 2 decimal places |
| 400 | `invalid_currency` | Currency code is not a 3-letter alphabetic string |
| 400 | `same_currency` | `from` and `to` are identical |
| 400 | `future_date` | Requested date is after today |
| 400 | `date_out_of_range` | Requested date is before 1999-01-04 |
| 400 | `unsupported_currency` | Currency format is valid but not recognised by upstream |
| 504 | `upstream_timeout` | Upstream did not respond within 3 seconds |
| 502 | `upstream_error` | Network failure, upstream 5xx, or rate-limit (429) |
| 502 | `invalid_upstream_response` | Upstream reply is non-JSON, missing fields, non-positive rate, or has a future `rate_date` |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | TCP port the service listens on |
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | Base URL for the Frankfurter v1 API |
