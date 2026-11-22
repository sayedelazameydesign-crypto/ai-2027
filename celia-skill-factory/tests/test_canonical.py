"""Canonical serialization: reproducible bytes, rejected floats, no dup keys."""
import unittest

from reference import canonical


class TestCanonical(unittest.TestCase):
    def test_key_order_is_irrelevant(self):
        self.assertEqual(canonical.dumps({"b": 1, "a": 2}), canonical.dumps({"a": 2, "b": 1}))

    def test_no_whitespace(self):
        self.assertEqual(canonical.dumps({"a": 1, "b": [1, 2]}), '{"a":1,"b":[1,2]}')

    def test_digest_is_stable(self):
        a = canonical.digest({"sequence": 1, "event_type": "X"})
        b = canonical.digest({"event_type": "X", "sequence": 1})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_float_rejected(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.dumps({"ratio": 0.688})

    def test_nested_float_rejected(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.dumps({"a": [{"b": 1.5}]})

    def test_integers_and_strings_allowed(self):
        canonical.dumps({"n": 42, "s": "نص عربي", "b": True, "z": None})

    def test_duplicate_key_rejected(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.loads_strict('{"a":1,"a":2}')

    def test_bad_json_rejected(self):
        with self.assertRaises(canonical.CanonicalError):
            canonical.loads_strict("{not json")

    def test_roundtrip(self):
        obj = {"sequence": 3, "payload": {"k": [1, 2, 3]}}
        self.assertEqual(canonical.loads_strict(canonical.dumps(obj)), obj)

    def test_genesis_is_64_zeros(self):
        self.assertEqual(canonical.GENESIS, "0" * 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
