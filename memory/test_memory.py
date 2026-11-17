#!/usr/bin/env python3
"""Tests for the evidence-gated memory store — against the REAL kernel.

Run: python3 memory/test_memory.py -v
Stdlib only.
"""
from __future__ import annotations

import copy
import hashlib
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "command-hud"))

import kernel_bridge                     # noqa: E402
import store as ms                       # noqa: E402


class MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ai2027-mem-test-"))
        cls.srv = kernel_bridge.load(runtime=cls.tmp, fresh=True)
        cls.sandbox_root = cls.tmp / "sandboxes"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.store = ms.EvidenceMemoryStore(
            Path(tempfile.mkdtemp(dir=self.tmp)) / "m.sqlite",
            sandbox_root=self.sandbox_root)

    def run_goal(self, goal_type="create_file", **params):
        return self.srv.run_goal(goal_type, params)

    # ---------------------------- admission ------------------------------ #
    def test_01_verified_run_is_admitted_with_sha(self):
        rec = self.run_goal(path="a.txt", content="hello")
        v = self.store.ingest(rec)
        self.assertTrue(v.admitted)
        self.assertEqual(v.trust, 1.0)
        self.assertTrue(v.artifact_verified)
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(len(self.store.rows()[0]["sha256"]), 64)

    def test_02_denied_run_is_refused_and_logged(self):
        rec = self.run_goal(path="../../escape.txt", content="x")
        self.assertEqual(rec["status"], "DENIED")
        v = self.store.ingest(rec)
        self.assertFalse(v.admitted)
        self.assertIn("DENIED", v.reason)
        self.assertEqual(len(self.store.rows()), 0)
        log = self.store.log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["admitted"], 0)
        self.assertEqual(log[0]["raw_status"], "DENIED")

    def test_03_failed_run_is_refused(self):
        rec = self.run_goal("read_file", path="missing.txt")
        self.assertEqual(rec["status"], "FAILED")
        self.assertFalse(self.store.ingest(rec).admitted)
        self.assertEqual(len(self.store.rows()), 0)

    def test_04_tampered_record_is_refused_even_if_status_says_verified(self):
        rec = self.run_goal(path="b.txt", content="data")
        bad = copy.deepcopy(rec)
        bad["evidence"][0]["checks"]["hash_matches"] = False
        v = self.store.ingest(bad)
        self.assertFalse(v.admitted)
        self.assertIn("hash_matches", v.reason)

    def test_05_record_without_sha_is_refused(self):
        rec = self.run_goal(path="c.txt", content="data")
        bad = copy.deepcopy(rec)
        bad["observations"] = []
        v = self.store.ingest(bad)
        self.assertFalse(v.admitted)
        self.assertIn("SHA-256", v.reason)

    def test_06_duplicate_is_not_stored_twice(self):
        rec = self.run_goal(path="d.txt", content="same")
        self.assertTrue(self.store.ingest(rec).admitted)
        second = self.store.ingest(rec)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(self.store.rows()), 1)

    def test_07_artifact_tamper_is_detected_by_verify(self):
        rec = self.run_goal(path="e.txt", content="original")
        self.store.ingest(rec)
        art = Path(self.store.rows()[0]["artifact_path"])
        art.write_text("TAMPERED")
        v = self.store.verify()
        self.assertFalse(v["intact"])
        self.assertEqual(v["hash_mismatches"], 1)

    def test_08_missing_artifact_is_reported(self):
        rec = self.run_goal(path="f.txt", content="x")
        self.store.ingest(rec)
        Path(self.store.rows()[0]["artifact_path"]).unlink()
        v = self.store.verify()
        self.assertEqual(v["artifacts_missing"], 1)

    # ---------------------------- retrieval ------------------------------ #
    def test_09_search_ranks_exact_overlap_higher(self):
        self.store.ingest(self.run_goal(path="p/halftone.txt",
                                        content="halftone 55 lpi cmyk separation"))
        self.store.ingest(self.run_goal(path="o/schedule.txt",
                                        content="press queue lead time four days"))
        res = self.store.search("halftone lpi", k=2)
        self.assertIn("halftone", res["results"][0]["text"])
        self.assertTrue(res["results"][0]["above_noise"])

    def test_10_unrelated_query_has_no_confident_hit(self):
        for i in range(4):
            self.store.ingest(self.run_goal(path=f"n{i}.txt",
                                            content=f"fabric note number {i} cotton"))
        res = self.store.search("kubernetes cluster autoscaling", k=3)
        self.assertEqual(res["confident"], 0)

    def test_11_paraphrase_is_missed_honestly(self):
        """This test asserts a LIMITATION so nobody can claim synonyms work."""
        self.store.ingest(self.run_goal(path="h.txt",
                                        content="halftone 55 lpi cmyk separation"))
        res = self.store.search("dot pattern resolution for silk screens", k=3)
        self.assertEqual(res["confident"], 0,
                         "if this ever passes, re-measure recall and update docs")

    def test_12_arabic_normalisation_matches_variants(self):
        self.store.ingest(self.run_goal(path="ar.txt",
                                        content="فصل الألوان للطباعة الحريرية"))
        # alef-hamza + diacritics must normalise to the same tokens
        res = self.store.search("فصل الالوان للطباعه الحريريه", k=1)
        self.assertGreater(res["results"][0]["score"], 0.5)
        self.assertTrue(res["results"][0]["above_noise"])

    def test_13_embedding_is_deterministic(self):
        a = ms.embed("halftone 55 lpi")
        b = ms.embed("halftone 55 lpi")
        self.assertEqual(a, b)
        self.assertEqual(len(a), ms.DIM)
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=6)

    def test_14_noise_floor_is_measured_not_hardcoded(self):
        for i in range(3):
            self.store.ingest(self.run_goal(path=f"z{i}.txt",
                                            content=f"distinct topic {i} alpha beta"))
        floor = self.store.noise_floor()
        self.assertEqual(floor["pairs"], 3)
        self.assertIsNotNone(floor["p95"])

    # --------------------------- concurrency ----------------------------- #
    def test_15_concurrent_ingest_does_not_crash_or_duplicate(self):
        """Regression: sqlite3 objects are not thread-safe (found live)."""
        recs = [self.run_goal(path=f"t{i}.txt", content=f"thread {i}") for i in range(6)]
        errors = []

        def worker(r):
            try:
                self.store.ingest(r)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(r,)) for r in recs]
        for t_ in threads:
            t_.start()
        for t_ in threads:
            t_.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.rows()), 6)

    def test_16_stats_report_backend_and_absence_of_sqlite_vec(self):
        self.store.ingest(self.run_goal(path="s.txt", content="x"))
        st = self.store.stats()
        self.assertIn("sqlite3", st["backend"])
        self.assertIn("no sqlite-vec", st["backend"])
        self.assertEqual(st["memories"], 1)

    def test_17_refusals_are_durable_across_connections(self):
        """Regression: _log() did not commit, so refusals vanished and the
        'refused and logged' claim was false. A fresh connection must see it."""
        rec = self.run_goal(path="../../escape.txt", content="x")
        self.assertFalse(self.store.ingest(rec).admitted)
        reopen = ms.EvidenceMemoryStore(self.store.db_path,
                                       sandbox_root=self.store.sandbox_root)
        rows = reopen.log()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["admitted"], 0)
        self.assertEqual(rows[0]["raw_status"], "DENIED")
        self.assertEqual(reopen.stats()["refused_events"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
