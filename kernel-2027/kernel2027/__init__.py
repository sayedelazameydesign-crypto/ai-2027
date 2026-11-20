"""kernel-2027 — the one implementation of the primitives every engine needs.

Why this package exists
-----------------------
The repository had grown twelve engines that each solved the *same* reliability
problems locally: secrecy redaction in 24 files, retry/backoff in 39, circuit
breaking in 20, approval gates in 44, integrity hashing in 85. So a fix landed
in the engine under development and never reached the others — the exact failure
mode this package is built to end.

The kernel is the opposite of a framework: it is a small, dependency-free set of
**pure functions and tiny state machines** with no I/O policy of their own. An
engine keeps its own transport, storage and product decisions; it only stops
re-implementing what is genuinely identical everywhere.

Design rules (deliberately narrow)
----------------------------------
1. **stdlib only** — same promise as the rest of the repository, so any engine
   can import it without changing its dependency story.
2. **No global state, no imports between engines.** The kernel never imports an
   engine; engines import the kernel.
3. **Behaviour-preserving migration.** Each function is extracted from code that
   already ran in production paths (``fleet2027.contracts``, ``security.py``),
   so adoption is a rename, not a rewrite.
4. **Enforced, not advisory.** ``scripts/kernel_guard.py`` fails CI when an
   engine re-implements a promoted primitive. An unenforced rule is a wish.

What belongs here, and what does not
------------------------------------
Belongs: identifier/time/hash helpers, redaction, retry policy, circuit breaker,
approval decisions, idempotency keys, audit-chain links, endpoint safety.

Does **not** belong: HTTP clients, database connections, prompt engineering,
model choice, UI, anything that encodes a product decision. If two engines would
legitimately disagree, it is not kernel material.
"""

from __future__ import annotations

from .approvals import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    decide,
    require_approval,
)
from .audit import audit_link, verify_chain
from .circuit import BreakerState, CircuitBreaker, CircuitOpenError
from .contracts import (
    KERNEL_VERSION,
    KernelError,
    UnsafeValueError,
    canonical_json,
    clamp,
    require_safe_id,
    sha256_bytes,
    sha256_file,
    sha256_text,
    stable_id,
    utc_now,
)
from .endpoint import validate_endpoint
from .idempotency import IdempotencyKey, effect_key
from .redaction import SENSITIVE_KEYWORDS, redact, redact_mapping
from .retry import RetryPolicy, backoff_delay, with_retry

__all__ = [
    "KERNEL_VERSION",
    "SENSITIVE_KEYWORDS",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalState",
    "BreakerState",
    "CircuitBreaker",
    "CircuitOpenError",
    "IdempotencyKey",
    "KernelError",
    "RetryPolicy",
    "UnsafeValueError",
    "audit_link",
    "backoff_delay",
    "canonical_json",
    "clamp",
    "decide",
    "effect_key",
    "redact",
    "redact_mapping",
    "require_approval",
    "require_safe_id",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "stable_id",
    "utc_now",
    "validate_endpoint",
    "verify_chain",
    "with_retry",
]
