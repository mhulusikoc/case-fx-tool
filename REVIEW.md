# Review of tool.py

## 1. Silent Parameter Ignorance (Public Query Contract Broken)

- **What is wrong**: The documented endpoint expects parameters `from` and `date` (e.g., `?from=EUR&date=2026-08-28`). However, the function signature uses `from_` and `on`. Because FastAPI defaults missing parameters, `from_` silently defaults to `"EUR"` and `on` defaults to `None` (today).
- **Customer impact**: High. A customer asking to convert USD to TRY in 2020 will silently receive the conversion for EUR to TRY for today. Providing the wrong exchange rate while appearing successful is highly dangerous for financial calculations.
- **How I would verify it**: Send a `GET /tools/convert?amount=100&from=USD&date=2020-01-01`. The response will show `"from": "EUR"` and `"rate_date"` as today's date, confirming the inputs were ignored.

## 2. Cache / Rate-Date Provenance Broken

- **What is wrong**: The simple in-memory cache uses `f"{base}-{target}"` as the key. It completely ignores the requested date. Furthermore, it blindly echoes back `str(on or date.today())` instead of the actual `rate_date` from the upstream payload. The response also omits `asked_date`, so the caller loses the original requested date needed to explain any fallback.
- **Customer impact**: High. If one user requests the EUR to TRY rate for 2010, and a second user requests the EUR to TRY rate for 2024, the second user will receive the 2010 exchange rate. Even worse, the API will falsely claim this rate is from 2024, misleading the customer entirely.
- **How I would verify it**: Request `?amount=100&from_=EUR&to=TRY&on=2010-01-01`. Then immediately request `?amount=100&from_=EUR&to=TRY&on=2024-01-01`. The second request will return the exact same rate as the first, but with `"rate_date": "2024-01-01"`.

## 3. Upstream Failures Returned as Zero-Valued Success

- **What is wrong**: The `convert` endpoint wraps everything in a blanket `try...except Exception` block. On any failure (network timeout, 500 error, or an invalid currency causing a KeyError), it returns a successful HTTP 200 response with `"rate": 0.0` and `"result": 0.0`. 
- **Customer impact**: High. As stated in the brief, a wrong number is worse than no number. If the upstream provider is down, the language model will confidently tell the customer that their currency is worth 0.00, rather than explaining the service is temporarily unavailable.
- **How I would verify it**: Mock `client.get` to raise `httpx.TimeoutException` or return HTTP 500. The endpoint will swallow the error and still return a HTTP 200 OK with `"rate": 0.0` and `"result": 0.0`.

## 4. Premature Rate Rounding Changes Valid Results

- **What is wrong**: The upstream exchange rate is rounded to two decimal places before the conversion is calculated (`rate = round(rate, 2)`). This discards valid precision from the ECB rate before multiplication.
- **Customer impact**: Medium. Even with valid input and a healthy upstream, the service can return a financially incorrect conversion. With the sample rate `47.1234`, converting 250 produces `11780.00` after rounding the rate to `47.12`, while using the published rate produces `11780.85`.
- **How I would verify it**: Make the upstream return `47.1234` and convert an amount of `250`. Compare the endpoint result with `250 × 47.1234`; the current implementation returns `11780.00` instead of `11780.85`.

## The one I would fix before shipping tonight

**1. Silent Parameter Ignorance (Public Query Contract Broken)**
This defect happens deterministically on the normal, documented happy-path. Even if a customer provides the correct `from` and `date` parameters, the service ignores them and answers a completely different financial question. The cache problem requires a repeated/date-sensitive request, and the zero-value bug requires an upstream failure, but the contract mismatch directly breaks an ordinary, valid request.

## Things that look suspicious but are fine

Using an in-memory cache is not inherently a problem for this small service. The defect is not the existence of the cache; it is that the cache key omits the requested date and the cached value does not preserve the actual rate date provenance.

Rounding the final converted monetary result to two decimal places is also reasonable. The problem is specifically rounding the exchange rate itself before multiplication, which discards rate precision and can change the result.
