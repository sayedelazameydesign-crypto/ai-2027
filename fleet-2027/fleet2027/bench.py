"""Quality benchmark — run every fixed sentence, score every artifact.

What this answers that the gates cannot
---------------------------------------
``gate.py`` proves the fleet recovers, isolates and verifies. It says so itself:
*"not the quality of any LLM."* This module measures the other thing. It runs a
fixed corpus end to end and scores what actually landed on disk.

It does not know or care which backend produced the file. That is what makes the
number comparable: the same corpus, the same scorer, one variable — the brain.

The honesty rule this module is built around
--------------------------------------------
A benchmark that reports a number without saying what produced it is a rumour
with a decimal point. Every report therefore records:

* which backend ran (or ``none`` — the scaffold),
* whether a model was actually called (from the step's own evidence, not a flag),
* the corpus fingerprint, so two runs over different sentences cannot be
  compared by accident.

If a sentence fails to complete, it is **scored zero and counted**, never
dropped. Dropping failures is how a benchmark reports 100% while half the work
is missing.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel2027.intent import IntentError, resolve, to_task_spec
from kernel2027.quality import USABLE_FLOOR, score_file

from .contracts import PlanStep, TaskSpec
from .generalist import build_generalist_registry
from .runner import FleetRunner
from .store import DurableTaskStore

#: Where the fixed corpus lives, relative to the engine root.
DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "bench" / "sentences.json"


@dataclass(frozen=True)
class SentenceResult:
    """What happened to one sentence, and what it was worth."""

    sentence_id: str
    domain: str
    text: str
    status: str
    score: float
    usable: bool
    chars: int
    model_called: bool
    failures: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the report."""
        return {
            "id": self.sentence_id,
            "domain": self.domain,
            "text": self.text,
            "status": self.status,
            "score": round(self.score, 4),
            "usable": self.usable,
            "chars": self.chars,
            "model_called": self.model_called,
            "failures": list(self.failures),
            "error": self.error,
        }


@dataclass(frozen=True)
class BenchReport:
    """The whole run: every sentence plus the summary that gets cited."""

    backend: str
    model_called: bool
    corpus_sha256: str
    generated_at: str
    results: tuple[SentenceResult, ...] = field(default_factory=tuple)

    @property
    def completed(self) -> tuple[SentenceResult, ...]:
        """Sentences whose task actually finished."""
        return tuple(r for r in self.results if r.status == "succeeded")

    @property
    def mean_score(self) -> float:
        """Mean over *all* sentences, including failures scored zero.

        Averaging only the successes is the single easiest way to make a
        benchmark look better than the system is.
        """
        if not self.results:
            return 0.0
        return round(sum(r.score for r in self.results) / len(self.results), 4)

    @property
    def usable_rate(self) -> float:
        """Fraction of sentences that produced a usable artifact."""
        if not self.results:
            return 0.0
        return round(sum(1 for r in self.results if r.usable) / len(self.results), 4)

    @property
    def completion_rate(self) -> float:
        """Fraction of sentences whose task reached ``succeeded``."""
        if not self.results:
            return 0.0
        return round(len(self.completed) / len(self.results), 4)

    def mean_score_for(self, domain: str) -> float:
        """Mean score within one domain, for finding the weak spot."""
        rows = [r for r in self.results if r.domain == domain]
        return round(sum(r.score for r in rows) / len(rows), 4) if rows else 0.0

    def worst(self, limit: int = 5) -> tuple[SentenceResult, ...]:
        """The weakest sentences, worst first — the work queue, not a scoreboard."""
        return tuple(sorted(self.results, key=lambda r: r.score)[:limit])

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full report."""
        return {
            "backend": self.backend,
            "model_called": self.model_called,
            "corpus_sha256": self.corpus_sha256,
            "generated_at": self.generated_at,
            "usable_floor": USABLE_FLOOR,
            "summary": {
                "sentences": len(self.results),
                "mean_score": self.mean_score,
                "usable_rate": self.usable_rate,
                "completion_rate": self.completion_rate,
                "by_domain": {
                    d: self.mean_score_for(d)
                    for d in sorted({r.domain for r in self.results})
                },
            },
            "results": [r.to_dict() for r in self.results],
        }


def corpus_fingerprint(path: Path | str = DEFAULT_CORPUS) -> str:
    """SHA-256 of the corpus file, so two runs cannot be silently compared."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_corpus(path: Path | str = DEFAULT_CORPUS) -> tuple[dict[str, Any], ...]:
    """Read and validate the fixed corpus."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    sentences = payload.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError(f"{path}: no sentences")
    for item in sentences:
        for key in ("id", "domain", "text"):
            if not str(item.get(key, "")).strip():
                raise ValueError(f"{path}: sentence missing {key}: {item!r}")
    return tuple(sentences)


def run_benchmark(
    *,
    backend: Any = None,
    corpus: Path | str = DEFAULT_CORPUS,
    keep_workspace: str | None = None,
    limit: int | None = None,
    backends: Sequence[Any] | None = None,
) -> BenchReport:
    """Run every fixed sentence end to end and score the artifact that resulted.

    ``backend=None`` measures the deterministic scaffold — the baseline every
    real model must beat. ``keep_workspace`` retains the artifacts for
    inspection; without it everything is written to a temporary directory and
    discarded, so a benchmark run cannot pollute the repository.

    ``backends`` rotates one backend per sentence. Free providers each cap
    their daily requests below what the corpus costs, so a single backend
    cannot finish it; rotating spreads the corpus across independent quotas
    while keeping every sentence measured by a real model. A sentence that
    fails on its assigned backend is not retried on another one — that would
    quietly change which model produced which row.

    ``limit`` runs only the first N sentences. A free provider can cap daily
    requests below what the full corpus costs; a short run that records
    ``sentences`` still produces a comparable number for that subset, and the
    report says how many it measured. It is not a substitute for the full run.
    """
    sentences = load_corpus(corpus)
    if limit is not None and limit < len(sentences):
        sentences = sentences[:limit]
    fingerprint = corpus_fingerprint(corpus)
    if backends:
        # A rotated run is not any single backend; naming one would be a lie.
        backend_name = "rotate(" + ",".join(b.name for b in backends) + ")"
    else:
        backend_name = getattr(backend, "name", "none") if backend is not None else "none"
    called_any = False

    root = Path(keep_workspace) if keep_workspace else Path(tempfile.mkdtemp())
    root.mkdir(parents=True, exist_ok=True)
    results: list[SentenceResult] = []

    # A tap that refuses once will refuse for every sentence it is handed:
    # the cause (quota, wrong model id, rejected credential) is per provider,
    # not per sentence. Retrying it sentence after sentence turned one bad
    # tap into 8 wasted calls and hid the good ones behind it.
    live = list(backends) if backends else []
    turn = 0

    def _drop(dead) -> None:
        """Remove a tap the moment it proves it cannot answer.

        A build step and a collect-sources step can both target the same tap
        before the loop advances, so dropping only on the outer failure still
        cost three calls to a dead provider.
        """
        if dead in live:
            live.remove(dead)

    for item in sentences:
        if live:
            active = live[turn % len(live)]
            turn += 1
        else:
            active = backend
        sentence_id = str(item["id"])
        domain = str(item["domain"])
        text = str(item["text"])
        workspace = root / sentence_id
        workspace.mkdir(parents=True, exist_ok=True)
        db = str(workspace / "bench.db")

        try:
            resolution = resolve(text, task_key=sentence_id)
        except IntentError as exc:
            # The corpus names a domain; if the reader cannot recover it, the
            # reader is wrong and the report must say so rather than score zero
            # and move on. Counted as a failure, with the reason attached.
            results.append(
                SentenceResult(
                    sentence_id=sentence_id,
                    domain=domain,
                    text=text,
                    status="unreadable",
                    score=0.0,
                    usable=False,
                    chars=0,
                    model_called=False,
                    error=str(exc),
                )
            )
            continue

        spec = to_task_spec(
            resolution, task_id=sentence_id, build_step=PlanStep, build_spec=TaskSpec
        )
        try:
            with DurableTaskStore(db) as store:
                store.submit(spec)
                report = FleetRunner(
                    store=store,
                    registry=build_generalist_registry(backend=active),
                    workspace_root=workspace,
                    announce=False,
                ).run(sentence_id)
                step = store.step_result(sentence_id, f"{sentence_id}_build") or {}
                evidence = step.get("evidence") or {}
                called = bool(evidence.get("called"))
                status = report.status.value
                # The runner records a refusal as a failed task, not as an
                # exception, so the exception handler never saw it. A tap that
                # produced no model call on its sentence is dead for the rest
                # of the run.
                if not called and status != "succeeded":
                    _drop(active)
        except Exception as exc:  # noqa: BLE001 - a benchmark reports, never crashes
            if live and active in live:
                live.remove(active)
            results.append(
                SentenceResult(
                    sentence_id=sentence_id,
                    domain=domain,
                    text=text,
                    status="error",
                    score=0.0,
                    usable=False,
                    chars=0,
                    model_called=False,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                )
            )
            continue

        called_any = called_any or called
        produced = workspace / "intent" / sentence_id / _expected_file(resolution.domain)
        if not produced.exists():
            # "no artifact" describes the symptom. The step's own error says
            # why it never appeared — an LLM timeout, a refusal, a verifier
            # rejection — and that sentence is what makes a bad run diagnosable
            # instead of merely countable.
            step_error = (step or {}).get("error")
            reason = f"no artifact at {produced.name}"
            if step_error:
                reason = f"{reason}; step said: {str(step_error)[:150]}"
            results.append(
                SentenceResult(
                    sentence_id=sentence_id,
                    domain=resolution.domain,
                    text=text,
                    status=status,
                    score=0.0,
                    usable=False,
                    chars=0,
                    model_called=called,
                    error=reason,
                )
            )
            continue

        quality = score_file(resolution.domain, str(produced))
        results.append(
            SentenceResult(
                sentence_id=sentence_id,
                domain=resolution.domain,
                text=text,
                status=status,
                score=quality.score,
                usable=quality.usable,
                chars=quality.chars,
                model_called=called,
                failures=quality.failures(),
            )
        )

    return BenchReport(
        backend=backend_name,
        model_called=called_any,
        corpus_sha256=fingerprint,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        results=tuple(results),
    )


#: The artifact each domain must produce, mirroring the plan's `out` basename.
#: Kept here rather than imported so a change to the plan is caught by a test
#: instead of silently redirecting the scorer to a missing file.
_FILES: dict[str, str] = {
    "website": "index.html",
    "document": "document.md",
    "research": "report.md",
    "organize": "plan.md",
    "learn": "explanation.md",
    "travel": "itinerary.md",
    "business": "business-plan.md",
    "code": "change.md",
    "email": "draft.txt",
}


def _expected_file(domain: str) -> str:
    """The filename the plan promised for this domain."""
    return _FILES.get(domain, "notes.md")


def write_report(report: BenchReport, path: Path | str) -> Path:
    """Persist a report as JSON, creating parents as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_report(path: Path | str) -> dict[str, Any]:
    """Read a previously written report."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
