from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from substrate.security import ActorTokenManager


class ActorTokenManagerTests(unittest.TestCase):
    def test_token_is_stable_for_one_agent_and_scoped_to_namespace_name_and_uid(self):
        manager = ActorTokenManager("master-secret-for-tests-" + "m" * 32)
        token = manager.token_for("agent-substrate", "counter", "uid-1")
        self.assertEqual(token, manager.token_for("agent-substrate", "counter", "uid-1"))
        self.assertNotEqual(token, manager.token_for("other", "counter", "uid-1"))
        self.assertNotEqual(token, manager.token_for("agent-substrate", "other", "uid-1"))
        self.assertNotEqual(token, manager.token_for("agent-substrate", "counter", "uid-2"))
        self.assertGreaterEqual(len(token), 40)

    def test_short_master_key_and_empty_identity_are_rejected(self):
        with self.assertRaises(ValueError):
            ActorTokenManager("too-short")
        manager = ActorTokenManager(b"x" * 32)
        with self.assertRaises(ValueError):
            manager.token_for("agent-substrate", "counter", "")


if __name__ == "__main__":
    unittest.main()
