# kernel-2027 — one implementation of the primitives every engine shares

**Install (one line, any engine):** `pip install -e kernel-2027`

Without an install, add the repo root to `PYTHONPATH` and import
`kernel2027` — it is stdlib-only, so nothing else is required.

## Why it exists (the measured problem)

Before this package, the repository had **no shared core at all**: not one module
imported across engine boundaries, and every reliability primitive was copied per
engine.

| Concern | Files implementing it | Top engines |
|---|---:|---|
| hashing / integrity | **85** | projects 56, fleet 7, python-2027 6, scripts 6 |
| approval gate | **44** | projects 27, python-2027 6, browser 5, connectors 4 |
| retry / backoff | **39** | projects 19, fleet 11, python-2027 7 |
| secret redaction | **24** | python-2027 16, projects 6 |
| audit log | **21** | projects 15, fleet 6 |
| circuit breaker | **20** | projects 11, fleet 7 |
| idempotency | **18** | fleet 10, projects 8 |
| dead letter | **13** | fleet 12, scripts 1 |

The consequence was exactly what drove this work: a fix landed in the engine
under development and never reached the other eleven.

## What is in it

| Module | Primitive | Promoted from |
|---|---|---|
| `contracts` | `utc_now`, `sha256_*`, `canonical_json`, `require_safe_id`, `stable_id`, `clamp` | `fleet2027.contracts` |
| `redaction` | `redact`, `redact_mapping`, 11 credential patterns | `ai2027_protocol.security` |
| `retry` | `RetryPolicy`, `backoff_delay` (full jitter), `with_retry` | runner retry arithmetic |
| `circuit` | `CircuitBreaker`, `BreakerPool`, `CircuitOpenError` | `fleet2027.registry` |
| `approvals` | `ApprovalRequest`, `decide` (**fail-closed**), `require_approval` | approval paths in 4 engines |
| `idempotency` | `IdempotencyKey`, `effect_key` | ledger keys in 2 engines |
| `audit` | `audit_link`, `verify_chain` | `fleet2027.store` |
| `endpoint` | `validate_endpoint` (HTTPS, loopback-only HTTP) | `fleet2027.llm` |

## The rule that makes this stick

Documentation does not stop copy-paste. **`scripts/kernel_guard.py` fails CI**
when an engine re-implements a promoted primitive instead of importing it:

```
$ python3 scripts/kernel_guard.py
kernel-2027 adoption: 2/13 engines import the kernel
FAIL fleet-2027/fleet2027/registry.py: re-implements circuit breaker (use kernel2027.CircuitBreaker)
```

Adoption is ratcheted: the count may only go up. A new engine that copies a
primitive instead of importing it cannot merge.

## What deliberately stays *out*

An engine keeps its transport, storage schema, prompt strategy, product rules and
UI. If two engines would legitimately disagree, it is not kernel material —
putting it here would create a second, subtler form of the same problem.
