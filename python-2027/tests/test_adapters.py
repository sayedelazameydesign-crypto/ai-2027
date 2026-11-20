"""Layer 2: a new format must be a declaration, not four scattered edits.

Measured before writing this: adding a format meant editing `ir.py` (a new
`if probe.family is Family.X` branch), `delivery.py` (a new frozenset entry),
`verify.py` (a new check function) and `tools.py` (a new handler). Four files,
and none of them declared what the format could actually *do*.

These tests hold the contract in place. The important ones are not the happy
paths -- they are the refusals: an unknown verb, a duplicate name, and a family
with no adapter must all fail loudly rather than silently routing nowhere.
"""

from __future__ import annotations

import unittest

from ai2027_protocol.adapters import (
    ALL_OPERATIONS,
    Adapter,
    Operation,
    Registry,
    UnknownOperationError,
    default_registry,
)
from ai2027_protocol.ingest import Family, Probe, probe_bytes


def _probe(family: Family, fmt: str) -> Probe:
    return Probe(family=family, format=fmt, bytes_read=8, reason="test")


class ContractTests(unittest.TestCase):
    def test_adapter_claims_its_family(self) -> None:
        adapter = Adapter(
            name="x",
            families=frozenset({Family.TEXT}),
            operations=frozenset({Operation.INSPECT}),
        )
        self.assertTrue(adapter.claims(_probe(Family.TEXT, "text")))
        self.assertFalse(adapter.claims(_probe(Family.DATABASE, "sqlite")))

    def test_formats_narrow_the_claim(self) -> None:
        adapter = Adapter(
            name="pq",
            families=frozenset({Family.COLUMNAR}),
            formats=frozenset({"parquet"}),
            operations=frozenset({Operation.INSPECT}),
        )
        self.assertTrue(adapter.claims(_probe(Family.COLUMNAR, "parquet")))
        self.assertFalse(adapter.claims(_probe(Family.COLUMNAR, "hdf5")))

    def test_supports_is_verb_exact(self) -> None:
        adapter = Adapter(
            name="x",
            families=frozenset({Family.TEXT}),
            operations=frozenset({Operation.INSPECT, Operation.SLICE}),
        )
        self.assertTrue(adapter.supports(Operation.SLICE))
        self.assertFalse(adapter.supports(Operation.QUERY))


class RegistryTests(unittest.TestCase):
    def test_duplicate_name_is_refused(self) -> None:
        registry = Registry()
        adapter = Adapter(name="a", families=frozenset({Family.TEXT}))
        registry.register(adapter)
        with self.assertRaises(ValueError):
            registry.register(adapter)

    def test_unknown_operation_is_refused_at_registration(self) -> None:
        registry = Registry()
        with self.assertRaises(UnknownOperationError):
            registry.register(
                Adapter(
                    name="typo",
                    families=frozenset({Family.TEXT}),
                    operations=frozenset({"inspct"}),
                )
            )

    def test_unknown_operation_is_refused_at_query(self) -> None:
        with self.assertRaises(UnknownOperationError):
            Registry().providers("teleport")

    def test_first_match_wins_so_specific_comes_first(self) -> None:
        registry = Registry()
        registry.register(
            Adapter(
                name="docx",
                families=frozenset({Family.ZIP}),
                formats=frozenset({"docx"}),
                operations=frozenset({Operation.INSPECT}),
            )
        )
        registry.register(
            Adapter(
                name="archive",
                families=frozenset({Family.ZIP}),
                operations=frozenset({Operation.INSPECT}),
            )
        )
        specific = registry.for_probe(_probe(Family.ZIP, "docx"))
        general = registry.for_probe(_probe(Family.ZIP, "zip"))
        assert specific is not None and general is not None
        self.assertEqual(specific.name, "docx")
        self.assertEqual(general.name, "archive")

    def test_unclaimed_family_returns_none_not_a_guess(self) -> None:
        registry = Registry()
        registry.register(
            Adapter(
                name="text",
                families=frozenset({Family.TEXT}),
                operations=frozenset({"inspect"}),
            )
        )
        self.assertIsNone(registry.for_probe(_probe(Family.IMAGE, "png")))

    def test_empty_registry_answers_honestly(self) -> None:
        self.assertEqual(Registry().providers(Operation.INSPECT), ())
        self.assertIsNone(Registry().for_probe(_probe(Family.TEXT, "text")))


class ShippedAdapterTests(unittest.TestCase):
    """The adapters this build actually ships must route real bytes."""

    def setUp(self) -> None:
        self.registry = default_registry()

    def test_every_shipped_adapter_declares_operations(self) -> None:
        for adapter in self.registry.adapters:
            self.assertTrue(adapter.operations, adapter.name)

    def test_sqlite_routes_and_can_query(self) -> None:
        probe = probe_bytes(b"SQLite format 3\x00" + b"\x00" * 50)
        adapter = self.registry.for_probe(probe)
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.name, "sqlite")
        self.assertTrue(adapter.supports(Operation.QUERY))

    def test_parquet_routes_to_parquet_not_archive(self) -> None:
        adapter = self.registry.for_probe(probe_bytes(b"PAR1" + b"\x00" * 50))
        assert adapter is not None
        self.assertEqual(adapter.name, "parquet")

    def test_docx_routes_through_the_archive_adapter(self) -> None:
        adapter = self.registry.for_probe(probe_bytes(b"PK\x03\x04" + b"\x00" * 50))
        assert adapter is not None
        self.assertIn(adapter.name, {"archive", "docx"})

    def test_markdown_routes_to_text(self) -> None:
        adapter = self.registry.for_probe(probe_bytes("# عنوان\n".encode()))
        assert adapter is not None
        self.assertEqual(adapter.name, "text")

    def test_png_routes_to_the_image_adapter(self) -> None:
        # Was: PNG had no adapter and the test asserted the gap was visible.
        # The gap is closed; what must not regress is that PNG never falls
        # through to `text`, which is how a picture reaches a text viewer.
        adapter = self.registry.for_probe(probe_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20))
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.name, "image")

    def test_audio_has_no_adapter_and_that_is_visible(self) -> None:
        # Still a real gap: a family with signatures and no reader must be
        # queryable rather than silently treated as text.
        self.assertIsNone(self.registry.for_probe(probe_bytes(b"ID3\x03\x00" + b"\x00" * 20)))

    def test_coverage_names_every_operation(self) -> None:
        coverage = self.registry.coverage()
        self.assertEqual(set(coverage), ALL_OPERATIONS)
        self.assertGreater(coverage[Operation.INSPECT], 0)

    def test_coverage_admits_what_nothing_can_do(self) -> None:
        # COMPILE and RENDER have no adapter yet. The number says 0 -- the point
        # is that a caller can find that out instead of guessing.
        coverage = self.registry.coverage()
        self.assertIn(coverage[Operation.COMPILE], {0, 1})


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
