"""Locate the sibling ``kernel-2027`` package when running from a checkout.

Why this file exists
--------------------
``fleet2027`` builds on the shared kernel, so importing it needs ``kernel2027``
to be importable. Two situations must both work, and the second is the reason
this is not simply a ``pip install`` line in the docs:

1. **Installed** — a wheel, an editable install, or ``PYTHONPATH`` already makes
   ``kernel2027`` importable. Nothing is touched here, so an installed
   environment is never second-guessed by a directory that happens to sit
   nearby.
2. **Bare checkout** — ``git clone`` and run, with no pip step. The sibling
   ``kernel-2027/`` directory is added to ``sys.path``.

Subprocesses are the hard case: a spawned child (``multiprocessing`` *spawn*,
``python cli.py``) inherits neither the parent's ``sys.path`` edits nor pytest's
``conftest``, so it re-runs this module from scratch. Resolving from
``__file__`` makes that work in a child exactly as it does in the parent.

If no sibling checkout exists either, the caller's own ``import kernel2027``
raises its normal ``ImportError`` — this helper never substitutes a fake.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

#: Where the sibling package lives, relative to this file.
_SIBLING = Path(__file__).resolve().parent.parent.parent / "kernel-2027"


def ensure_kernel_importable() -> bool:
    """Make ``kernel2027`` importable from the sibling checkout if needed.

    Returns ``True`` when a ``sys.path`` entry was added, ``False`` when the
    kernel was already importable or is simply not present beside this checkout.
    Uses ``find_spec`` rather than ``try: import`` so the probe neither executes
    the module nor hides a failure behind a suspended ``except``.
    """
    if importlib.util.find_spec("kernel2027") is not None:
        return False
    if not (_SIBLING / "kernel2027" / "__init__.py").is_file():
        return False
    entry = str(_SIBLING)
    if entry in sys.path:
        return False
    sys.path.insert(0, entry)
    return True


ensure_kernel_importable()
