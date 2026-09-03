# Notes

## Decisions

**`Decimal` for all financial arithmetic.**  
`float` arithmetic is lossy; using `Decimal` throughout — and converting the upstream JSON value via `str()` before parsing — avoids silent rounding errors in both the rate and the final result.

**Weekend / holiday behaviour: use the most recently published ECB rate, but never hide it.**  
Frankfurter automatically shifts a weekend or holiday request back to the nearest prior trading day. The response always returns the actual `rate_date` alongside the `asked_date`, so the caller can tell the customer exactly which day's number they are seeing. Silently presenting a Friday rate as if it were a Sunday rate would be misleading.

**Fail closed on malformed or untrusted upstream data.**  
Any upstream response that is non-JSON, missing required fields, contains a non-positive rate, or carries a `rate_date` in the future relative to `asked_date` returns `502 invalid_upstream_response`. The endpoint never invents a fallback value.

**3-second upstream timeout.**  
The service surfaces `504 upstream_timeout` rather than hanging indefinitely.

**Simple in-memory cache for successful rate lookups.**  
Cache key: `(from_currency, to_currency, asked_date)`. `amount` is excluded — the cache stores the rate, not the final converted value, so any amount can reuse it. Errors are never cached, ensuring a transient failure does not permanently block a valid pair.

**HTTP 4xx from upstream is split into two cases.**  
`400 / 404 / 422` → `unsupported_currency` (Frankfurter uses these for unknown symbols). `429 / 401 / 403` and other 4xx → `upstream_error` (provider-side problems that have nothing to do with the currency).

**No database, Redis, Docker, auth, or UI** — none were required, and a smaller thing done carefully beats unnecessary complexity.

---

## With Another Day

- Bounded cache with TTL so stale rates eventually expire.
- Single-flight / request coalescing so concurrent identical requests don't all hit upstream simultaneously.
- Stronger currency validation against a known ECB symbol list, returning `unsupported_currency` before touching the network.
- Structured logging and basic metrics (request count, cache hit rate, upstream latency).
- Packaging improvements: `pyproject.toml`, pinned lockfile, smoke-test in CI.

---

## AI Tools

Multiple AI assistants were used at different stages:

- **OpenAI Codex** — initial implementation scaffolding.
- **Claude (Sonnet)** — iterative implementation, test design, and edge-case coverage.
- **Gemini (3.1 Pro)** — production-oriented review of `tool.py`, reproducing candidate defects and helping rank the Part B findings by customer impact.
- **ChatGPT** — requirement interpretation, adversarial edge-case review, and final cross-check of both Part A and Part B against the case brief.

In all cases, AI output was manually inspected, run against the test suite, and verified against the case brief before committing. The commit history reflects a step-by-step process rather than a single generated dump.

---

## One Thing the AI Got Wrong

In the initial error-handling pass, the AI mapped all upstream HTTP 4xx responses to `unsupported_currency`. That made sense for `404` (unknown symbol), but `429 Too Many Requests` is a rate-limit from the provider — telling the customer their currency is "not supported" because Frankfurter is throttling us would be wrong and confusing.

During review, this issue was identified: provider-side problems (`429`, `401`, `403`) should surface as `upstream_error` rather than `unsupported_currency`, and the status-code branching was updated accordingly. A dedicated test — `test_svc_http_429_raises_upstream_error_not_unsupported_currency` — was added to lock in the correct behaviour.
