# agi-kernel (local build)

Evidence-first agent kernel, packaged from `projects/agi-system` **without copying code
by hand** — `build_wheel.sh` syncs the source at build time (imports inside the kernel
are relative, so the rename is safe and exact).

```bash
./build_wheel.sh          # produces dist/agi_kernel-0.1.0-*.whl
pip install dist/*.whl    # local install
agi-kernel --help         # CLI, same as python -m core in the source repo
```

⚠️ **License pending** — not published to PyPI. See `docs/LICENSE-DECISIONS.md`.
