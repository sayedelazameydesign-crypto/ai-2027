"""Locate the sibling ``kernel-2027`` package when running from a checkout.

``browser_skill_adapter`` uses the shared kernel, so importing it needs ``kernel2027`` to be
importable. An installed environment is checked first and never second-guessed;
otherwise the sibling checkout is added to ``sys.path`` so that a bare
``git clone`` works without any pip step — the repository's standing promise.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_kernel_importable() -> bool:
    """Add the sibling kernel to ``sys.path`` if it is not already importable.

    Returns ``True`` when a path was added. If no sibling checkout exists the
    real ``ImportError`` surfaces from the caller's own import, so a genuinely
    missing dependency is never disguised by this helper.
    """
    try:
        import kernel2027  # noqa: F401 - the import is the probe

        return False
    except ImportError:
        pass

    candidate = Path(__file__).resolve().parent.parent / "kernel-2027"
    entry = str(candidate)
    if (candidate / "kernel2027" / "__init__.py").is_file() and entry not in sys.path:
        sys.path.insert(0, entry)
        return True
    return False


ensure_kernel_importable()
