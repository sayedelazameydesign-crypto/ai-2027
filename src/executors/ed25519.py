"""Ed25519 (RFC 8032) — pure-stdlib verify, plus sign for tests/tooling.

Why hand-rolled: the repository's zero-dependency rule (ADR-0002) forbids
third-party crypto in the executor path, and the CI gate runs every P1 test
in a bare stdlib environment. The implementation follows the RFC 8032
arithmetic (affine twisted-Edwards points, SHA-512 based) and is validated
by the official §7.1 test vectors in tests/test_ed25519.py plus an
independent OpenSSL cross-check.

Security notes:
- ``verify`` rejects non-canonical S (S >= group order) and points that are
  not on the curve, per RFC 8032 §5.1.7.
- ``sign`` / ``secret_public_key`` exist for tests and offline tooling only;
  production signing happens in the Cloudflare Worker (docs/adr/0006).
"""

from __future__ import annotations

import hashlib
from typing import Optional

# Field and group parameters (RFC 8032 §5.1).
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_SQRT_M1 = pow(2, (_Q - 1) // 4, _Q)


def _inv(x: int) -> int:
    return pow(x, _Q - 2, _Q)


def _xrecover(y: int) -> int:
    """Recover the even x for a given y on the curve."""
    xx = (y * y - 1) * _inv((_D * y * y + 1) % _Q)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _SQRT_M1) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


# Base point B (RFC 8032 §5.1): y = 4/5, x recovered even.
_BY = (4 * _inv(5)) % _Q
_B = (_xrecover(_BY), _BY)


def _on_curve(p: Optional[tuple]) -> bool:
    if p is None:
        return False
    x, y = p
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _Q == 0


def _add(p: tuple, r: tuple) -> tuple:
    x1, y1 = p
    x2, y2 = r
    x3 = (x1 * y2 + x2 * y1) * _inv((1 + _D * x1 * x2 * y1 * y2) % _Q)
    y3 = (y1 * y2 + x1 * x2) * _inv((1 - _D * x1 * x2 * y1 * y2) % _Q)
    return (x3 % _Q, y3 % _Q)


def _scalar_mult(p: tuple, e: int) -> tuple:
    if e == 0:
        return (0, 1)
    q = _scalar_mult(p, e // 2)
    q = _add(q, q)
    if e & 1:
        q = _add(q, p)
    return q


def _encode_point(p: tuple) -> bytes:
    x, y = p
    out = bytearray(y.to_bytes(32, "little"))
    out[31] |= (x & 1) << 7
    return bytes(out)


def _decode_point(data: bytes) -> Optional[tuple]:
    if len(data) != 32:
        return None
    packed = int.from_bytes(data, "little")
    sign = (packed >> 255) & 1
    y = packed & ((1 << 255) - 1)
    if y >= _Q:
        return None
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    point = (x, y)
    return point if _on_curve(point) else None


def _clamp(secret: bytes) -> int:
    if len(secret) != 32:
        raise ValueError("ed25519 secret key must be 32 bytes")
    a = bytearray(secret)
    a[0] &= 248
    a[31] &= 63
    a[31] |= 64
    return int.from_bytes(bytes(a), "little")


def secret_public_key(secret: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte seed (tests/tooling)."""
    h = hashlib.sha512(secret).digest()
    a = _clamp(h[:32])
    return _encode_point(_scalar_mult(_B, a))


def sign(message: bytes, secret: bytes) -> bytes:
    """Produce a 64-byte RFC 8032 signature (tests/tooling only)."""
    h = hashlib.sha512(secret).digest()
    a = _clamp(h[:32])
    prefix = h[32:]
    public = _encode_point(_scalar_mult(_B, a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little")
    big_r = _encode_point(_scalar_mult(_B, r))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(),
                       "little")
    s = (r + k * a) % _L
    return big_r + s.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, public_key: bytes) -> bool:
    """RFC 8032 §5.1.7 verification. Any structural problem -> False."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    big_r = _decode_point(signature[:32])
    public = _decode_point(public_key)
    if big_r is None or public is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                      # reject malleable encodings
        return False
    k = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(),
        "little") % _L
    left = _scalar_mult(_B, s)
    right = _add(big_r, _scalar_mult(public, k))
    return left == right
