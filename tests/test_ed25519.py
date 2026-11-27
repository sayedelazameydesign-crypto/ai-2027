#!/usr/bin/env python3
"""RFC 8032 conformance + tamper tests for the pure-stdlib Ed25519 module.

The official §7.1 vectors are the hermetic core: a transcription or math
error cannot pass them. An optional OpenSSL cross-check (skipped when the
binary is absent) additionally proves the implementation against an
independent, widely reviewed implementation.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "executors" / "ed25519.py"

_spec = importlib.util.spec_from_file_location("ed25519", MODULE_PATH)
assert _spec and _spec.loader
ed25519 = importlib.util.module_from_spec(_spec)
sys.modules["ed25519"] = ed25519
_spec.loader.exec_module(ed25519)

# RFC 8032 §7.1 — Ed25519 test vectors 1..3.
RFC_VECTORS = [
    {
        "secret": "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "public": "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "message": "",
        "signature": ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522"
                      "4901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655"
                      "141438e7a100b"),
    },
    {
        "secret": "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "public": "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "message": "72",
        "signature": ("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223"
                      "ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aee"
                      "b00d291612bb0c00"),
    },
    {
        "secret": "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "public": "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "message": "af82",
        # NOTE: the signature ends …ec40a (confirmed against OpenJDK,
        # Wycheproof and asecuritysite renders of RFC 8032 §7.1).
        "signature": ("6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7"
                      "db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28d"
                      "c027beceea1ec40a"),
    },
]


class TestRfc8032Vectors(unittest.TestCase):
    def test_verify_all_official_vectors(self):
        for idx, vec in enumerate(RFC_VECTORS, start=1):
            with self.subTest(vector=idx):
                ok = ed25519.verify(
                    bytes.fromhex(vec["signature"]),
                    bytes.fromhex(vec["message"]),
                    bytes.fromhex(vec["public"]),
                )
                self.assertTrue(ok, "vector %d must verify" % idx)

    def test_public_key_derivation_matches_vectors(self):
        for idx, vec in enumerate(RFC_VECTORS, start=1):
            with self.subTest(vector=idx):
                derived = ed25519.secret_public_key(bytes.fromhex(vec["secret"]))
                self.assertEqual(derived.hex(), vec["public"])

    def test_sign_matches_official_signatures(self):
        # Same secret + message must reproduce the exact official signature
        # (deterministic RFC 8032 signing).
        for idx, vec in enumerate(RFC_VECTORS, start=1):
            with self.subTest(vector=idx):
                sig = ed25519.sign(bytes.fromhex(vec["message"]),
                                   bytes.fromhex(vec["secret"]))
                self.assertEqual(sig.hex(), vec["signature"])


class TestTampering(unittest.TestCase):
    def _fresh(self):
        secret = bytes(range(32))
        public = ed25519.secret_public_key(secret)
        message = b"governed-write assertion"
        signature = ed25519.sign(message, secret)
        return secret, public, message, signature

    def test_roundtrip_accepts(self):
        _, public, message, signature = self._fresh()
        self.assertTrue(ed25519.verify(signature, message, public))

    def test_bit_flips_rejected(self):
        _, public, message, signature = self._fresh()
        for position in (0, 31, 32, 63):
            broken = bytearray(signature)
            broken[position] ^= 0x01
            self.assertFalse(
                ed25519.verify(bytes(broken), message, public),
                "flipped bit %d must not verify" % position)

    def test_wrong_message_rejected(self):
        _, public, message, signature = self._fresh()
        self.assertFalse(ed25519.verify(signature, message + b"x", public))

    def test_wrong_public_key_rejected(self):
        _, _, message, signature = self._fresh()
        other_public = ed25519.secret_public_key(b"\x01" * 32)
        self.assertFalse(ed25519.verify(signature, message, other_public))

    def test_non_canonical_s_rejected(self):
        # S >= L is malleable and must fail even though the group equation
        # would otherwise hold for S+L in the raw arithmetic.
        _, public, message, signature = self._fresh()
        s = int.from_bytes(signature[32:], "little")
        big_r = signature[:32]
        forged = big_r + (s + (2**252 + 27742317777372353535851937790883648493)
                          ).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(forged, message, public))

    def test_structural_garbage_rejected(self):
        _, public, message, _ = self._fresh()
        self.assertFalse(ed25519.verify(b"\x00" * 64, message, public))
        self.assertFalse(ed25519.verify(b"\x00" * 63, message, public))
        self.assertFalse(ed25519.verify(b"\x00" * 64, message, b"\x00" * 31))


class TestOpenSSLCrossCheck(unittest.TestCase):
    """Sign with OpenSSL, verify with ours — and the reverse."""

    def setUp(self):
        if shutil.which("openssl") is None:
            self.skipTest("openssl binary not available")

    def test_openssl_signature_verifies_in_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key.pem"
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "Ed25519",
                 "-out", str(key)],
                check=True, capture_output=True)
            message = b"cross-check payload"
            msg_file = Path(tmp) / "msg.bin"
            sig_file = Path(tmp) / "sig.bin"
            msg_file.write_bytes(message)
            subprocess.run(
                ["openssl", "pkeyutl", "-sign", "-inkey", str(key),
                 "-rawin", "-in", str(msg_file), "-out", str(sig_file)],
                check=True, capture_output=True)
            pub_pem = subprocess.run(
                ["openssl", "pkey", "-in", str(key), "-pubout"],
                check=True, capture_output=True).stdout
            pub_der = subprocess.run(
                ["openssl", "pkey", "-pubin", "-inform", "PEM",
                 "-outform", "DER", "-in", "/dev/stdin"],
                input=pub_pem, check=True, capture_output=True).stdout
            # SPKI Ed25519 DER: last 32 bytes are the raw public key.
            public_raw = pub_der[-32:]
            self.assertTrue(ed25519.verify(sig_file.read_bytes(), message,
                                           public_raw))

    def test_python_signature_verifies_in_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = bytes.fromhex(RFC_VECTORS[2]["secret"])
            message = b"reverse cross-check"
            signature = ed25519.sign(message, secret)
            pub_raw = ed25519.secret_public_key(secret)
            # Build a SPKI DER for the raw key: fixed12-byte prefix + key.
            pub_der = bytes.fromhex("302a300506032b6570032100") + pub_raw
            pub_file = Path(tmp) / "pub.pem"
            pem = subprocess.run(
                ["openssl", "pkey", "-pubin", "-inform", "DER",
                 "-outform", "PEM"],
                input=pub_der, check=True, capture_output=True).stdout
            pub_file.write_bytes(pem)
            sig_file = Path(tmp) / "sig.bin"
            msg_file = Path(tmp) / "msg.bin"
            sig_file.write_bytes(signature)
            msg_file.write_bytes(message)
            result = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin",
                 "-inkey", str(pub_file), "-rawin",
                 "-in", str(msg_file), "-sigfile", str(sig_file)],
                capture_output=True)
            self.assertIn(b"Success", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
