"""The Supabase Edge port of CELIA must embed today's persona and vectors.

``supabase/functions/celia-telegram`` is generated from this package by
``scripts/celia_edge_parity_vectors.py``; editing the persona here without
regenerating would let the two runtimes drift. The Deno tests then prove the
TypeScript behaviour against the regenerated vectors.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "celia_edge_parity_vectors", ROOT / "scripts" / "celia_edge_parity_vectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_edge_function_embeds_the_current_python_persona() -> None:
    gen = _generator()
    source = gen.INDEX.read_text(encoding="utf-8")
    assert gen.render_index(source) == source, "run scripts/celia_edge_parity_vectors.py --write"


def test_parity_vectors_are_regenerated() -> None:
    gen = _generator()
    assert gen.VECTORS.read_text(encoding="utf-8") == gen.vectors_text()
