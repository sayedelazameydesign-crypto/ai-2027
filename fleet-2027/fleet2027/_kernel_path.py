"""Locate the sibling ``kernel-2027`` package when running from a checkout.

Why this file exists
--------------------
``fleet2027`` builds on the shared kernel, so ``import fleet2027`` needs
``kernel2027`` to be importable. Three situations must all work, and one of them
is the reason this module is not simply a ``pip install`` line in the docs:

1. **In-repo, no install** — ``python3 fleet-2027/cli.py`` and
   ``pytest fleet-2027/tests``. The repository's whole promise is "clone and
   run", so requiring a prior ``pip install -e kernel-2027`` would break it.
2. **Subprocesses** — ``cli.py`` is executed as a child process by the
   multi-process gates. A child inherits neither ``sys.path`` edits nor the
   parent's pytest ``conftest``.
3. **Installed** — if ``kernel2027`` is importable already (wheel, editable
   install, or ``PYTHONPATH``), this module does nothing at all.

The third case is checked first, so an installed environment is never
second-guessed by a stray directory next door.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_kernel_importable() -> bool:
    """Add the sibling kernel to ``sys.path`` if it is not already importable.

    Returns ``True`` when a path was added. Deliberately silent and idempotent:
    this is a packaging affordance, not a feature, and it must never mask a
    genuinely missing dependency — if no sibling checkout exists, the import
    error surfaces from the real ``import kernel2027`` with its own message.
    """
    try:
        import kernel2027  # noqa: F401 - the import is the probe

        return False
    except ImportError:
        pass

    # fleet-2027/fleet2027/_kernel_path.py -> fleet-2027/fleet2027 -> fleet-2027
    # -> repository root -> kernel-2027/
    candidate = Path(__file__).resolve().parent.parent.parent / "kernel-2027"
    entry = str(candidate)
    if (candidate / "kernel2027" / "__init__.py").is_file() and entry not in sys.path:
        sys.path.insert(0, entry)
        return True
    return False


ensure_kernel_importable()
