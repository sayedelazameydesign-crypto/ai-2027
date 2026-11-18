"""Narrow local bridge to the evidence-first kernel, not a browser executor.

Run the kernel in a separate interpreter because both projects use top-level
packages named ``core`` and ``memory``. Only create_file is supported.
"""
from pathlib import Path
import json
import subprocess
import sys

from .tool_executor import ToolExecutor


class VerifiedFileExecutor(ToolExecutor):
    def __init__(self, runtime_dir):
        self.runtime_dir = Path(runtime_dir).resolve()

    def execute(self, params):
        if not isinstance(params, dict) or set(params) != {"id", "digest"}:
            raise ValueError("approval id and digest are required")
        if not all(isinstance(params[k], str) for k in ("id", "digest")):
            raise ValueError("approval id and digest must be strings")
        worker = Path(__file__).with_name("verified_worker.py")
        try:
            proc = subprocess.run(
                [sys.executable, str(worker), str(self.runtime_dir)],
                input=json.dumps(params), text=True, capture_output=True,
                timeout=30, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("kernel worker timed out; outcome is unknown") from exc
        if proc.returncode:
            raise RuntimeError("kernel worker failed; outcome may be unknown")
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid kernel worker response") from exc
        if not isinstance(result, dict) or "state" not in result:
            raise RuntimeError("invalid kernel worker response")
        return result
