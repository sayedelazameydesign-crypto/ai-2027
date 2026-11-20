"""Make `fleet2027` and its sibling `kernel2027` importable under bare pytest.

The fleet package now builds on the shared kernel, so a checkout must be able
to run its tests **without installing anything** — that is the promise
`fleet-2027/README.md` makes and the CI unit job enforces ("stdlib only, no
package install").

Two layouts have to work:

* in-repo, running ``pytest fleet-2027/tests`` from anywhere
* a clean wheel/checkout where only ``kernel-2027/`` sits beside ``fleet-2027/``

Putting the paths on ``sys.path`` here — rather than requiring ``pip install
-e`` — keeps the "zero third-party dependencies" claim literally true: the
kernel is stdlib-only, so importing it needs no install either.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent      # fleet-2027/tests
FLEET_ROOT = TESTS_ROOT.parent                    # fleet-2027
REPO_ROOT = FLEET_ROOT.parent                     # repository root
KERNEL_ROOT = REPO_ROOT / "kernel-2027"           # sibling package

for candidate in (FLEET_ROOT, KERNEL_ROOT):
    entry = str(candidate)
    if candidate.is_dir() and entry not in sys.path:
        sys.path.insert(0, entry)
