"""Phase-0 hardware readiness inspection and independent Ollama verification harness."""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from .ai_providers import ProviderError
from .celia_memory import CeliaMemoryStore, OllamaEmbeddingBackend
from .contracts import StrictModel
from .ollama_layer import (
    HardwareProfileSpec,
    OllamaRuntimeClient,
    resolve_hardware_profile,
)
from .tools import SandboxToolRuntime


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _StructuredProbeReply(StrictModel):
    """Schema used to verify Ollama JSON Schema + Pydantic V2 strictness."""

    status: Literal["ok"]
    language: str = Field(min_length=2, max_length=32)
    summary: str = Field(min_length=3, max_length=240)


class HostResourceSnapshot(StrictModel):
    """Measured host hardware, RAM, disk, and Ollama CLI availability."""

    os_name: str
    python_version: str
    cpu_count: int = Field(ge=1)
    total_ram_gb: float | None = Field(default=None, ge=0.0)
    available_ram_gb: float | None = Field(default=None, ge=0.0)
    free_disk_gb: float = Field(ge=0.0)
    ollama_binary_found: bool
    recommended_profile: Literal["4gb-laptop", "8gb-standard", "colab-gpu"]
    recommended_chat_model: str
    recommended_coder_model: str
    recommended_embed_model: str
    recommended_num_ctx: int
    ram_advisories: tuple[str, ...] = ()


class CapabilityCheckResult(StrictModel):
    """Single independently measured verification check."""

    check_id: str
    passed: bool
    live_endpoint: bool
    latency_ms: float = Field(default=0.0, ge=0.0)
    evidence: str


class OllamaVerificationReport(StrictModel):
    """Complete Phase-0 + live Ollama readiness report without fake VERIFIED claims."""

    verification_status: Literal["VERIFIED", "LOCAL_GUARDS_ONLY", "UNREACHABLE", "FAILED_PROBES"]
    endpoint_configured: bool
    endpoint_reachable: bool
    host: HostResourceSnapshot
    profile: HardwareProfileSpec
    tags_count: int = Field(default=0, ge=0)
    running_count: int = Field(default=0, ge=0)
    checks: tuple[CapabilityCheckResult, ...] = ()
    honest_note: str


def _read_ram_gb() -> tuple[float | None, float | None]:
    """Read total and available RAM in GiB using stdlib OS interfaces."""
    sys_name = platform.system().lower()
    if sys_name == "windows" and hasattr(ctypes, "windll"):
        try:
            mem = _MemoryStatusEx()
            mem.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                gb = 1024**3
                return (round(mem.ullTotalPhys / gb, 2), round(mem.ullAvailPhys / gb, 2))
        except OSError:
            return None, None
    proc_mem = Path("/proc/meminfo")
    if proc_mem.is_file():
        total_kb: int | None = None
        avail_kb: int | None = None
        for line in proc_mem.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
        kb_to_gb = 1024 * 1024
        return (
            round(total_kb / kb_to_gb, 2) if total_kb is not None else None,
            round(avail_kb / kb_to_gb, 2) if avail_kb is not None else None,
        )
    return None, None


def inspect_host_readiness(target_path: Path | None = None) -> HostResourceSnapshot:
    """Measure host RAM, free disk, CPU, and recommend a safe Ollama model profile."""
    check_path = (target_path or Path.cwd()).resolve()
    disk = shutil.disk_usage(check_path)
    free_disk_gb = round(disk.free / (1024**3), 2)
    total_ram_gb, available_ram_gb = _read_ram_gb()
    if total_ram_gb is not None and total_ram_gb <= 5.5:
        profile = resolve_hardware_profile("4gb-laptop")
    elif total_ram_gb is not None and total_ram_gb >= 12.0:
        profile = resolve_hardware_profile("8gb-standard")
    else:
        profile = resolve_hardware_profile()

    advisories: list[str] = []
    if total_ram_gb is not None and total_ram_gb <= 5.5:
        advisories.append(
            "<=5.5GB total RAM detected: start strictly with qwen3.5:0.8b + num_ctx=2048."
        )
        advisories.append("Do not run 3B/7B models or load chat + embedding models concurrently.")
    if available_ram_gb is not None and available_ram_gb < 1.8:
        advisories.append(
            f"Low free RAM ({available_ram_gb:.2f} GB): close background apps before pull."
        )
    if free_disk_gb < 3.0:
        advisories.append(
            f"Low free disk ({free_disk_gb:.2f} GB): verify >=3 GB before pulling models."
        )
    if not advisories:
        advisories.append(profile.avoid_models_note)

    return HostResourceSnapshot(
        os_name=f"{platform.system()} {platform.release()}".strip(),
        python_version=sys.version.split()[0],
        cpu_count=max(1, os.cpu_count() or 1),
        total_ram_gb=total_ram_gb,
        available_ram_gb=available_ram_gb,
        free_disk_gb=free_disk_gb,
        ollama_binary_found=shutil.which("ollama") is not None,
        recommended_profile=profile.name,
        recommended_chat_model=profile.chat_model,
        recommended_coder_model=profile.coder_model,
        recommended_embed_model=profile.embed_model,
        recommended_num_ctx=profile.num_ctx,
        ram_advisories=tuple(advisories),
    )


async def verify_ollama_capabilities(
    *,
    client: OllamaRuntimeClient | None = None,
    workspace_root: Path | None = None,
    run_live_inference: bool = True,
) -> OllamaVerificationReport:
    """Independently test Ollama runtime, structured outputs, policy gate, and RAG persistence."""
    runtime = client or OllamaRuntimeClient()
    root = (workspace_root or Path.cwd()).resolve()
    host = await asyncio.to_thread(inspect_host_readiness, root)
    checks: list[CapabilityCheckResult] = []
    endpoint_configured = runtime.is_configured()
    endpoint_reachable = False
    tags_count = 0
    running_count = 0

    if endpoint_configured:
        t0 = time.perf_counter_ns()
        try:
            tags = await runtime.list_tags()
            mem_guard = await runtime.check_memory_guard()
            endpoint_reachable = True
            tags_count = len(tags)
            running_count = mem_guard.running_count
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_runtime_tags_and_ps",
                    passed=True,
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t0) / 1e6, 2),
                    evidence=(
                        f"tags={tags_count}, running={running_count}, "
                        f"safe_to_load_another={mem_guard.safe_to_load_another}"
                    ),
                )
            )
        except ProviderError as exc:
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_runtime_tags_and_ps",
                    passed=False,
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t0) / 1e6, 2),
                    evidence=f"Ollama endpoint check failed: {exc.code}",
                )
            )
    else:
        checks.append(
            CapabilityCheckResult(
                check_id="ollama_runtime_tags_and_ps",
                passed=True,
                live_endpoint=False,
                evidence="OLLAMA_BASE_URL blank (offline $0 default; no daemon assumed).",
            )
        )

    if endpoint_reachable and run_live_inference:
        # Structured output + Pydantic V2 verification
        t1 = time.perf_counter_ns()
        try:
            structured = await runtime.chat_structured(
                "Return JSON with status='ok', language='ar-en', and a 5-word summary.",
                _StructuredProbeReply,
            )
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_structured_pydantic_v2",
                    passed=structured.status == "ok",
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t1) / 1e6, 2),
                    evidence=f"Validated schema reply language={structured.language!r}",
                )
            )
        except ProviderError as exc:
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_structured_pydantic_v2",
                    passed=False,
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t1) / 1e6, 2),
                    evidence=f"Structured check failed: {exc.code}",
                )
            )

        # Tool calling Policy Gate verification (must allow safe read tool and block shell)
        t2 = time.perf_counter_ns()
        try:
            specs = SandboxToolRuntime().specs()
            tool_res = await runtime.chat_with_tools(
                "Inspect python-2027 directory using workspace.list_files only.",
                specs,
            )
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_tool_calling_policy_gate",
                    passed=tool_res.all_proposals_safe,
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t2) / 1e6, 2),
                    evidence=(
                        f"proposed={len(tool_res.proposed_calls)}, "
                        f"rejected={len(tool_res.rejected_calls)}"
                    ),
                )
            )
        except ProviderError as exc:
            checks.append(
                CapabilityCheckResult(
                    check_id="ollama_tool_calling_policy_gate",
                    passed=False,
                    live_endpoint=True,
                    latency_ms=round((time.perf_counter_ns() - t2) / 1e6, 2),
                    evidence=f"Tool probe failed: {exc.code}",
                )
            )

    # Independent SQLite RAG check (neural when reachable, else honest lexical fallback)
    t3 = time.perf_counter_ns()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        sample_doc = tmp_root / "notes.md"
        sample_text = (
            "# CeliaOS Memory\n\n"
            "Ollama provides local embeddings while Policy Gate retains execution.\n"
        )
        await asyncio.to_thread(sample_doc.write_text, sample_text, encoding="utf-8")
        db_file = tmp_root / "celia_probe.sqlite3"
        backend = (
            OllamaEmbeddingBackend(runtime) if (endpoint_reachable and run_live_inference) else None
        )
        store = CeliaMemoryStore(db_file, backend=backend, allow_lexical_fallback=True)
        idx_summary = await store.index_paths(tmp_root, ["notes.md"])
        # Re-open from disk to prove persistence across restarts
        reopened = CeliaMemoryStore(db_file, backend=backend, allow_lexical_fallback=True)
        hits = await reopened.search("Policy Gate local embeddings", k=3)
        rag_ok = idx_summary.total_chunks >= 1 and len(hits) >= 1
        checks.append(
            CapabilityCheckResult(
                check_id="celia_memory_sqlite_persistence",
                passed=rag_ok,
                live_endpoint=idx_summary.is_neural,
                latency_ms=round((time.perf_counter_ns() - t3) / 1e6, 2),
                evidence=(
                    f"mode={idx_summary.retrieval_mode}, chunks={idx_summary.total_chunks}, "
                    f"hits={len(hits)}"
                ),
            )
        )

    if endpoint_reachable and all(c.passed for c in checks):
        status: Literal["VERIFIED", "LOCAL_GUARDS_ONLY", "UNREACHABLE", "FAILED_PROBES"] = (
            "VERIFIED"
        )
        honest_note = "Independent live Ollama probes and persistent RAG verification passed."
    elif endpoint_reachable:
        status = "FAILED_PROBES"
        honest_note = "Ollama endpoint responded, but live probes failed; refused to mark VERIFIED."
    elif endpoint_configured and not endpoint_reachable:
        status = "UNREACHABLE"
        honest_note = "OLLAMA_BASE_URL is configured but unreachable; refused to mark VERIFIED."
    else:
        status = "LOCAL_GUARDS_ONLY"
        honest_note = (
            "OLLAMA_BASE_URL is not configured; only offline host/RAG guards ran (not VERIFIED)."
        )

    return OllamaVerificationReport(
        verification_status=status,
        endpoint_configured=endpoint_configured,
        endpoint_reachable=endpoint_reachable,
        host=host,
        profile=runtime.profile,
        tags_count=tags_count,
        running_count=running_count,
        checks=tuple(checks),
        honest_note=honest_note,
    )
