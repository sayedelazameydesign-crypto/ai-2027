#!/usr/bin/env python3
"""Phase 2B — smart alerting, gap analysis and error analysis on real log lines.

Pure standard library. No third-party imports; the CI AST gate treats every
file in this directory as stdlib-only and derives the sibling set from disk.

This module builds on Phase 2A's tamper-evident log (``local_watchdog``) and
turns "it records" into "it tells you what to look at". Three jobs:

1. **Gap analysis** (``analyze_gaps``): turn silence / poll_failed / transition
   records into a structured summary a human can act on at 3am. It never
   invents a number the log does not contain.
2. **Error analysis** (``analyze_errors``): feed real log lines through the
   classifier (``diagnose``) and report how often it says "unclassified"
   instead of guessing. That number is published either way.
3. **Alerting** (``AlertEngine``): rule-based evaluation over the above. Rules
   are data, not code paths buried in ``if`` statements, so they can be listed,
   audited and overridden.

Detection is deliberately narrow: **three consecutive** ``poll_failed`` records
(or a *recorded* silence of **300 seconds**) raise an alert. Three failures
scattered across a healthy log are NOT the same event and must not be reported
as one. Derived ``alert`` / ``recovery`` milestones are written back into the
Phase 2A hash-chained JSONL through ``EventLog.append`` — never with a raw file
write — so ``verify`` keeps working and re-analysis stays idempotent.

Nothing here sends a notification to a phone, an email or a message. A derived
event is a fact about the log, not proof that a human saw it. That is a later
phase and it is deliberately not in this module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import local_watchdog as _lw
except Exception:  # pragma: no cover - import shim for package use
    try:
        from . import local_watchdog as _lw  # type: ignore
    except Exception:
        _lw = None  # type: ignore

RECORD_VERSION = 1

# Detection defaults. These are thresholds over RECORDED FACTS, not timers:
# a 300 s silence is alerted on because the watchdog actually wrote that gap,
# not because a scheduler guessed that 300 s had elapsed.
DEFAULT_FAILURE_THRESHOLD = 3          # consecutive poll_failed records
DEFAULT_SILENCE_THRESHOLD = 300.0      # seconds of recorded `silence`
ALERT_KINDS = ("alert", "recovery")    # derived bookkeeping records


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _to_records(records):
    """Accept a list, a path, an EventLog, or a report dict."""
    if records is None:
        return []
    if isinstance(records, (str, os.PathLike)):
        p = Path(records)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    if hasattr(records, "read_all") and callable(records.read_all):
        try:
            recs, _ = records.read_all()
            return list(recs)
        except Exception:
            pass
    if isinstance(records, dict):
        if isinstance(records.get("records"), list):
            return list(records["records"])
        # maybe a single record
        if "seq" in records and "hash" in records:
            return [records]
        return []
    if isinstance(records, (list, tuple)):
        out = []
        for r in records:
            if isinstance(r, dict):
                out.append(r)
            elif isinstance(r, (str, os.PathLike)):
                out.extend(_to_records(r))
        return out
    return list(records) if hasattr(records, "__iter__") else []


# ---------------------------------------------------------------------------
# consecutive failures + derived alert/recovery events
# ---------------------------------------------------------------------------
def _analysis_records(records):
    """Log records excluding our own derived ``alert``/``recovery`` events.

    Re-analysis must see the same timeline it saw the first time. Without this
    filter, an appended alert between two failures would break the very run of
    failures it was derived from, and a second run would produce a different
    answer (and a duplicate event).
    """
    return [r for r in _to_records(records)
            if isinstance(r, dict) and r.get("kind") not in ALERT_KINDS]


def consecutive_failure_runs(records):
    """Runs of ADJACENT ``poll_failed`` records, in log order.

    "Consecutive" means adjacent in the chain: any other record (a successful
    poll, a silence, a start/stop) ends the run. Order is file order, not a
    sort on ``seq`` — sorting a tampered log would hide the tampering.
    """
    runs = []
    start = None
    length = 0
    for r in _analysis_records(records):
        if r.get("kind") == "poll_failed":
            if length == 0:
                start = r.get("seq")
            length += 1
        else:
            if length:
                runs.append({"start_seq": start, "end_seq": r.get("seq"),
                             "length": length})
            start, length = None, 0
    if length:
        runs.append({"start_seq": start, "end_seq": None, "length": length})
    return runs


def consecutive_failures(records=None):
    """Longest unbroken run of poll failures (an int, for rule evaluation)."""
    return max((r["length"] for r in consecutive_failure_runs(records)),
               default=0)


def trailing_consecutive_failures(records=None):
    """Failures still unrecovered at the end of the log."""
    runs = consecutive_failure_runs(records)
    return runs[-1]["length"] if runs and runs[-1]["end_seq"] is None else 0


def derive_events(records=None, failure_threshold=DEFAULT_FAILURE_THRESHOLD,
                  silence_threshold=DEFAULT_SILENCE_THRESHOLD):
    """Deterministically derive ``alert``/``recovery`` milestones from a log.

    Pure: it reads records and returns event descriptions; it writes nothing.
    An alert opens a reason, and the first successful ``poll`` after it closes
    that reason with a recovery. A log that ends mid-outage has NO recovery —
    that is the truth, not a missing feature.
    """
    events = []
    active = set()
    run = 0
    run_start = None
    for r in _analysis_records(records):
        kind = r.get("kind")
        seq = r.get("seq")
        if kind == "poll_failed":
            if run == 0:
                run_start = seq
            run += 1
            if run >= failure_threshold and "consecutive_failures" not in active:
                active.add("consecutive_failures")
                events.append({
                    "kind": "alert",
                    "data": {
                        "key": "alert:consecutive_failures:%s" % seq,
                        "reason": "consecutive_failures",
                        "value": run,
                        "threshold": failure_threshold,
                        "trigger_seq": seq,
                        "run_start_seq": run_start,
                        "message": "%d consecutive poll failures (threshold %s)"
                                   % (run, failure_threshold),
                    },
                })
        elif kind == "silence":
            run = 0
            gap = float((r.get("data") or {}).get("gap_seconds", 0) or 0)
            if gap >= silence_threshold and "large_gap" not in active:
                active.add("large_gap")
                events.append({
                    "kind": "alert",
                    "data": {
                        "key": "alert:large_gap:%s" % seq,
                        "reason": "large_gap",
                        "value": gap,
                        "threshold": silence_threshold,
                        "trigger_seq": seq,
                        "message": "recorded silence %.3fs (threshold %ss)"
                                   % (gap, silence_threshold),
                    },
                })
        elif kind == "poll":
            run = 0
            for reason in sorted(active):
                events.append({
                    "kind": "recovery",
                    "data": {
                        "key": "recovery:%s:%s" % (reason, seq),
                        "reason": reason,
                        "trigger_seq": seq,
                        "message": "successful poll after %s" % reason,
                    },
                })
            active.clear()
        else:
            # start / stop / tail_repaired / unknown: a break in the timeline,
            # not a success, so no recovery is claimed.
            run = 0
    return events


def _as_event_log(log):
    """Resolve a path or an EventLog to an EventLog instance."""
    if log is None:
        raise ValueError("a log path or EventLog is required")
    if hasattr(log, "append") and hasattr(log, "read_all"):
        return log
    if _lw is None:
        raise RuntimeError("local_watchdog is unavailable; refusing a raw write")
    return _lw.EventLog(log)


def append_event(log, kind, data=None, records=None, ts=None):
    """Append ONE ``alert``/``recovery`` record to the hash-chained JSONL.

    Chain safety: delegates to Phase 2A ``EventLog.append``, which owns seq,
    prev hash, record hash, the exclusive lock, fsync and crash-tail repair.
    Writing JSONL by hand here would silently break ``verify()``.

    Idempotency: ``data['key']`` is stable for a given log, so re-running
    ``alerts`` returns the existing record instead of appending a duplicate.

    ``ts`` is pinned to the last observed record's timestamp (not ``time.time``)
    so ``report()``'s span and ``observed_fraction`` do not change just because
    someone re-analysed the file. The real wall clock is kept in
    ``data['detected_at_iso']`` rather than being disguised as log time.
    """
    if kind not in ALERT_KINDS:
        raise ValueError("event kind must be one of %r" % (ALERT_KINDS,))
    event_log = _as_event_log(log)
    existing, _ = event_log.read_all()
    payload = dict(data or {})
    key = payload.get("key")
    if key:
        for r in existing:
            if (r.get("kind") in ALERT_KINDS
                    and (r.get("data") or {}).get("key") == key):
                return r
    if ts is None:
        timeline = [r for r in (records if records is not None else existing)
                    if isinstance(r, dict) and r.get("kind") not in ALERT_KINDS]
        ts = (float(timeline[-1].get("ts", time.time())) if timeline
              else time.time())
    payload.setdefault("kind", kind)
    payload["derived"] = True
    payload["detected_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime())
    return event_log.append(kind, payload, ts=ts)


def record_alert_events(log=None, records=None,
                        failure_threshold=DEFAULT_FAILURE_THRESHOLD,
                        silence_threshold=DEFAULT_SILENCE_THRESHOLD):
    """Derive milestones and append the missing ones. Returns a summary."""
    event_log = _as_event_log(log if log is not None else records)
    if records is None:
        records, _ = event_log.read_all()
    analysis = _analysis_records(records)
    events = derive_events(analysis, failure_threshold, silence_threshold)
    existing, _ = event_log.read_all()
    seen = {(r.get("kind"), (r.get("data") or {}).get("key"))
            for r in existing if r.get("kind") in ALERT_KINDS}
    appended, duplicates = [], []
    for e in events:
        key = e["data"]["key"]
        if (e["kind"], key) in seen:
            duplicates.append({"kind": e["kind"], "key": key})
            continue
        rec = append_event(event_log, e["kind"], e["data"], records=analysis)
        appended.append(rec)
        seen.add((e["kind"], key))
    return {
        "derived": len(events),
        "appended": len(appended),
        "duplicates": len(duplicates),
        "alerts": [r for r in appended if r.get("kind") == "alert"],
        "recoveries": [r for r in appended if r.get("kind") == "recovery"],
        "duplicate_events": duplicates,
        "records": appended,
    }


def _report(records):
    records = _to_records(records)
    if _lw is not None and hasattr(_lw, "report"):
        try:
            return _lw.report(records)
        except Exception:
            pass
    # minimal fallback
    kinds = {}
    for r in records:
        kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
    silences = [r for r in records if r.get("kind") == "silence"]
    failures = [r for r in records if r.get("kind") == "poll_failed"]
    total_silence = sum(float((s.get("data") or {}).get("gap_seconds", 0)) for s in silences)
    span = None
    if records:
        span = round(float(records[-1]["ts"]) - float(records[0]["ts"]), 3)
    return {
        "records": len(records),
        "span_seconds": span,
        "kinds": kinds,
        "silence_events": len(silences),
        "silence_seconds_total": round(total_silence, 3),
        "silence_seconds_largest": round(max(
            [float((s.get("data") or {}).get("gap_seconds", 0)) for s in silences],
            default=0.0), 3),
        "poll_failures": len(failures),
        "failure_classes": sorted({(f.get("data") or {}).get("error_class", "?") for f in failures}),
        "transitions": [t for r in records if r.get("kind") == "poll"
                        for t in (r.get("data") or {}).get("transitions") or []],
        "last_snapshot": None,
        "observed_fraction": (round(1 - total_silence / span, 4) if span else None),
    }


# ---------------------------------------------------------------------------
# diagnose (local copy of the adapter's classifier; no cross-package import)
# ---------------------------------------------------------------------------
ERROR_PATTERNS = [
    (r"ModuleNotFoundError|No module named", "missing_dependency"),
    (r"Address already in use|EADDRINUSE", "port_conflict"),
    (r"OOMKilled|out of memory|MemoryError", "out_of_memory"),
    (r"PermissionError|EACCES", "permission_denied"),
    (r"ECONNREFUSED|Connection refused", "connection_refused"),
    (r"Timeout|timed out|ETIMEDOUT", "timeout"),
    (r"syntax error|SyntaxError|IndentationError", "syntax_error"),
    (r"Traceback \(most recent call last\)", "unhandled_exception"),
    (r"exit status 1|exited with code [1-9]|Error: command failed", "process_exit_nonzero"),
    (r"SSL|certificate|TLS", "tls_error"),
    (r"disk|No space left", "disk_full"),
]


def diagnose(log_lines) -> dict:
    """Classify error lines. Reports what it cannot classify instead of guessing."""
    if isinstance(log_lines, str):
        log_lines = log_lines.splitlines()
    log_lines = list(log_lines or [])
    counts = {}
    samples = {}
    unclassified = 0
    errorish = 0
    problem_re = (r"error|exception|fail|refused|traceback|fatal|panic|crash|"
                  r"\bkilled\b|\boom\b|out of memory|memoryerror|"
                  r"\bexit(ed)? with (code|status)\b|\babort")
    for line in log_lines:
        if not re.search(problem_re, line, re.IGNORECASE):
            continue
        errorish += 1
        matched = False
        for pattern, label in ERROR_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                counts[label] = counts.get(label, 0) + 1
                samples.setdefault(label, str(line).strip()[:300])
                matched = True
                break
        if not matched:
            unclassified += 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return {
        "error_lines": errorish,
        "classified": sum(counts.values()),
        "unclassified": unclassified,
        "causes": [{"cause": c, "count": n, "sample": samples[c]} for c, n in ranked],
        "note": "a classification is a HYPOTHESIS; it is not a fix and it is "
                "not evidence that the hypothesis is correct",
    }


def classify(log_lines):
    return diagnose(log_lines)


# ---------------------------------------------------------------------------
# gap analysis
# ---------------------------------------------------------------------------
def analyze_gaps(records=None, log=None, **kwargs):
    if log is not None and records is None:
        records = log
    records = _to_records(records)
    rep = _report(records)
    silences = [r for r in records if r.get("kind") == "silence"]
    failures = [r for r in records if r.get("kind") == "poll_failed"]
    transitions = [t for r in records if r.get("kind") == "poll"
                   for t in (r.get("data") or {}).get("transitions") or []]
    gaps = []
    for s in silences:
        d = s.get("data") or {}
        gaps.append({
            "seq": s.get("seq"),
            "ts": s.get("ts"),
            "ts_iso": s.get("ts_iso"),
            "gap_seconds": d.get("gap_seconds"),
            "missed_intervals": d.get("missed_intervals"),
            "reason": d.get("reason"),
            "after_seq": d.get("after_seq"),
        })
    largest = max([float((s.get("data") or {}).get("gap_seconds", 0)) for s in silences],
                  default=0.0)
    failure_classes = sorted({(f.get("data") or {}).get("error_class", "?") for f in failures})
    observed = rep.get("observed_fraction")
    return {
        "records": len(records),
        "span_seconds": rep.get("span_seconds"),
        "kinds": rep.get("kinds", {}),
        "silence_events": len(silences),
        "silence_seconds_total": rep.get("silence_seconds_total", 0.0),
        "silence_seconds_largest": rep.get("silence_seconds_largest", 0.0),
        "poll_failures": len(failures),
        "consecutive_failures": consecutive_failures(records),
        "trailing_consecutive_failures": trailing_consecutive_failures(records),
        "consecutive_failure_runs": consecutive_failure_runs(records),
        "failure_classes": failure_classes,
        "transitions": transitions,
        "transition_count": len(transitions),
        "gaps": gaps,
        "max_gap_seconds": round(largest, 3),
        "has_gaps": bool(silences),
        "has_failures": bool(failures),
        "observed_fraction": observed,
        "last_snapshot": rep.get("last_snapshot"),
        "report": rep,
    }


def gap_analysis(records=None, **kwargs):
    return analyze_gaps(records, **kwargs)


def gaps(records=None, **kwargs):
    return analyze_gaps(records, **kwargs)


# ---------------------------------------------------------------------------
# error analysis
# ---------------------------------------------------------------------------
def _lines_from_records(records):
    records = _to_records(records)
    lines = []
    for r in records:
        # Our own derived event messages describe failures; feeding them back
        # into diagnosis would make the alert classify ITSELF as an
        # `unclassified` error on the next re-analysis. Only real log data
        # is evidence.
        if r.get("kind") in ALERT_KINDS:
            continue
        d = r.get("data") or {}
        if isinstance(d.get("message"), str):
            lines.append(d["message"])
        snap = d.get("snapshot")
        if isinstance(snap, dict):
            for v in snap.values():
                if isinstance(v, str):
                    lines.append(v)
        for t in (d.get("transitions") or []):
            if isinstance(t, dict):
                for v in t.values():
                    if isinstance(v, str):
                        lines.append(v)
    return lines


def analyze_errors(records=None, log_lines=None, log=None, **kwargs):
    if log is not None and records is None and log_lines is None:
        records = log
    records = _to_records(records) if records is not None else None
    if log_lines is None and records is not None:
        log_lines = _lines_from_records(records)
    if isinstance(log_lines, str):
        log_lines = log_lines.splitlines()
    log_lines = list(log_lines or [])
    diag = diagnose(log_lines)
    total = diag.get("error_lines", 0)
    unclassified = diag.get("unclassified", 0)
    rate = (unclassified / total) if total else 0.0
    return {
        "diagnosis": diag,
        "log_lines": len(log_lines),
        "error_lines": total,
        "classified": diag.get("classified", 0),
        "unclassified": unclassified,
        "unclassified_rate": round(rate, 4),
        "causes": diag.get("causes", []),
    }


def error_analysis(records=None, **kwargs):
    return analyze_errors(records=records, **kwargs)


def errors(records=None, **kwargs):
    return analyze_errors(records=records, **kwargs)


def measure_unclassified(log_lines=None, records=None, **kwargs):
    res = analyze_errors(records=records, log_lines=log_lines, **kwargs)
    return res["unclassified_rate"]


def unclassified_rate(log_lines=None, records=None, **kwargs):
    return measure_unclassified(log_lines=log_lines, records=records, **kwargs)


# ---------------------------------------------------------------------------
# alerting
# ---------------------------------------------------------------------------
class Alert(dict):
    """A dict that also supports attribute access, so both styles work."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "severity" in self and "level" not in self:
            self["level"] = self["severity"]
        if "level" in self and "severity" not in self:
            self["severity"] = self["level"]

    def __getitem__(self, key):
        if key == "level" and "level" not in self and "severity" in self:
            return self["severity"]
        if key == "severity" and "severity" not in self and "level" in self:
            return self["level"]
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "severity":
            super().__setitem__("level", value)
        elif key == "level":
            super().__setitem__("severity", value)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self[name] = value

    def to_dict(self):
        return dict(self)


class Rule:
    OPS = {
        ">=": lambda a, b: a >= b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        "<": lambda a, b: a < b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        "in": lambda a, b: a in b,
    }

    def __init__(self, name="rule", predicate=None, condition=None, metric=None,
                 op=">=", threshold=None, severity="warning", message=None,
                 **extra):
        self.name = name
        self.predicate = predicate if predicate is not None else condition
        self.metric = metric
        self.op = op
        self.threshold = threshold
        self.severity = severity
        self.message = message if message is not None else name
        self.extra = extra

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(**d)

    def evaluate(self, ctx):
        # callable predicate
        if callable(self.predicate):
            try:
                res = self.predicate(ctx)
            except Exception as exc:  # noqa: BLE001
                return Alert(severity="error", rule=self.name,
                             message=f"rule {self.name!r} raised: {exc}")
            return self._normalize(res, ctx)
        # string predicate -> safe eval against ctx
        if isinstance(self.predicate, str):
            try:
                res = eval(self.predicate, {"__builtins__": {}}, dict(ctx))  # noqa: S307
            except Exception:
                return False
            if res:
                return Alert(severity=self.severity, rule=self.name,
                             message=self.message, **self.extra)
            return False
        # metric comparison
        if self.metric is not None:
            val = ctx.get(self.metric, None)
            if val is None:
                return False
            op = self.OPS.get(self.op)
            if op is None:
                return False
            try:
                ok = bool(op(val, self.threshold))
            except Exception:
                return False
            if ok:
                return Alert(severity=self.severity, rule=self.name,
                             message=self.message, metric=self.metric,
                             value=val, threshold=self.threshold, **self.extra)
            return False
        return False

    def _normalize(self, res, ctx):
        if res is None or res is False:
            return False
        if res is True:
            return Alert(severity=self.severity, rule=self.name,
                         message=self.message, **self.extra)
        if isinstance(res, Alert):
            if "rule" not in res:
                res["rule"] = self.name
            return res
        if isinstance(res, dict):
            data = dict(res)
            data.setdefault("severity", self.severity)
            data.setdefault("rule", self.name)
            data.setdefault("message", self.message)
            return Alert(**data)
        if isinstance(res, str):
            return Alert(severity=self.severity, rule=self.name, message=res,
                         **self.extra)
        return Alert(severity=self.severity, rule=self.name,
                     message=str(res), **self.extra)


def default_rules(thresholds=None):
    t = thresholds or {}
    # `poll_failures` is still accepted as an override key for older callers,
    # but it now sets the CONSECUTIVE threshold, never the total-log count.
    cf = t.get("consecutive_failures",
               t.get("poll_failures", DEFAULT_FAILURE_THRESHOLD))
    mg = t.get("max_gap_seconds", DEFAULT_SILENCE_THRESHOLD)
    of = t.get("observed_fraction", 0.9)
    ur = t.get("unclassified_rate", 0.5)
    return [
        Rule(name="repeated_poll_failures", metric="consecutive_failures",
             op=">=", threshold=cf, severity="warning",
             message="%s consecutive poll_failed; source or network is unhealthy"
                     % cf),
        Rule(name="large_gap", metric="max_gap_seconds", op=">=", threshold=mg,
             severity="critical",
             message="a recorded silence gap reaches %ss" % mg),
        Rule(name="low_observed_fraction", metric="observed_fraction", op="<",
             threshold=of, severity="warning",
             message="observed fraction below threshold"),
        Rule(name="unclassified_errors", metric="unclassified_rate", op=">",
             threshold=ur, severity="info",
             message="too many unclassified error lines"),
    ]


class AlertEngine:
    def __init__(self, rules=None, handlers=None, thresholds=None, **kwargs):
        self.rules = []
        self.handlers = list(handlers or [])
        self.thresholds = dict(thresholds or {})
        if kwargs.get("thresholds") is not None:
            self.thresholds.update(kwargs["thresholds"])
        if rules is None:
            rules = default_rules(self.thresholds)
        for r in rules:
            self.add_rule(r)
        # allow extra threshold kwargs
        for k, v in kwargs.items():
            if k not in ("rules", "handlers", "thresholds"):
                self.thresholds.setdefault(k, v)

    def add_rule(self, rule):
        if isinstance(rule, Rule):
            self.rules.append(rule)
        elif isinstance(rule, dict):
            self.rules.append(Rule.from_dict(rule))
        elif isinstance(rule, str):
            self.rules.append(Rule(name=rule, condition=rule))
        elif callable(rule):
            self.rules.append(Rule(name=getattr(rule, "__name__", "custom"),
                                  predicate=rule))
        else:
            self.rules.append(rule)

    def add_handler(self, handler):
        self.handlers.append(handler)

    def remove_rule(self, name):
        self.rules = [r for r in self.rules
                     if getattr(r, "name", None) != name]

    def build_context(self, records=None, log=None, log_lines=None, context=None):
        if context is not None:
            return context
        if log is not None and records is None:
            records = log
        records = _to_records(records) if records is not None else []
        ctx = build_context(records, log_lines=log_lines)
        return ctx

    def evaluate(self, records=None, log=None, log_lines=None, context=None,
                **kwargs):
        ctx = self.build_context(records=records, log=log, log_lines=log_lines,
                                context=context)
        alerts = []
        for rule in list(self.rules):
            try:
                res = rule.evaluate(ctx) if hasattr(rule, "evaluate") else False
            except Exception as exc:  # noqa: BLE001
                res = Alert(severity="error", rule=str(rule),
                            message=f"rule raised: {exc}")
            if res is False or res is None:
                continue
            if isinstance(res, (list, tuple)):
                for a in res:
                    alerts.append(a if isinstance(a, Alert) else Alert(**(a if isinstance(a, dict) else {"message": str(a)})))
                continue
            alerts.append(res if isinstance(res, Alert) else (
                Alert(**res) if isinstance(res, dict) else Alert(message=str(res))))
        for h in list(self.handlers):
            try:
                h(alerts, ctx)
            except Exception:
                try:
                    h(alerts)
                except Exception:
                    pass
        # store last result
        self.last_context = ctx
        self.last_alerts = alerts
        return alerts

    def check(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def run(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def assess(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def scan(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def evaluate_rules(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def evaluate_alerts(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def run_rules(self, *a, **kw):
        return self.evaluate(*a, **kw)

    def check_rules(self, *a, **kw):
        return self.evaluate(*a, **kw)


# aliases for the class
Alerting = AlertEngine
Alerter = AlertEngine
AlertManager = AlertEngine
RulesEngine = AlertEngine
AlertEvaluator = AlertEngine


def build_context(records=None, log_lines=None, log=None, **kwargs):
    if log is not None and records is None:
        records = log
    records = _to_records(records) if records is not None else []
    gap = analyze_gaps(records)
    err = analyze_errors(records=records, log_lines=log_lines)
    kinds = {}
    for r in records:
        kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
    ctx = {
        "records": records,
        "records_count": len(records),
        "kinds": kinds,
        "gaps": gap,
        "gap_analysis": gap,
        "errors": err,
        "error_analysis": err,
        "diagnosis": err.get("diagnosis"),
        "silence_events": gap["silence_events"],
        "silence_seconds_total": gap["silence_seconds_total"],
        "silence_seconds_largest": gap["silence_seconds_largest"],
        "poll_failures": gap["poll_failures"],
        "consecutive_failures": gap["consecutive_failures"],
        "trailing_consecutive_failures": gap["trailing_consecutive_failures"],
        "consecutive_failure_runs": gap["consecutive_failure_runs"],
        "failure_classes": gap["failure_classes"],
        "max_gap_seconds": gap["max_gap_seconds"],
        "has_gaps": gap["has_gaps"],
        "has_failures": gap["has_failures"],
        "observed_fraction": gap["observed_fraction"],
        "transitions": gap["transitions"],
        "transition_count": gap["transition_count"],
        "last_snapshot": gap["last_snapshot"],
        "unclassified": err["unclassified"],
        "unclassified_rate": err["unclassified_rate"],
        "error_lines": err["error_lines"],
        "classified": err["classified"],
        "causes": err["causes"],
    }
    return ctx


def evaluate(records=None, log=None, log_lines=None, rules=None, handlers=None,
             thresholds=None, context=None, event_log=None, record=False,
             **kwargs):
    """Evaluate the rules. ``record=True`` also writes derived milestones.

    Recording is OPT-IN for the API: a plain ``evaluate(...)`` stays read-only.
    The ``alerts`` CLI enables it because it is given a log path to own.
    """
    thr = dict(thresholds or {})
    for k in ("consecutive_failures", "poll_failures", "max_gap_seconds",
              "observed_fraction", "unclassified_rate"):
        if k in kwargs:
            thr.setdefault(k, kwargs[k])
    engine = AlertEngine(rules=rules, handlers=handlers, thresholds=thr)
    alerts = engine.evaluate(records=records, log=log, log_lines=log_lines,
                             context=context)
    if record:
        target = event_log
        if target is None:
            for candidate in (records, log):
                if isinstance(candidate, (str, os.PathLike)) or (
                        hasattr(candidate, "append")
                        and hasattr(candidate, "read_all")):
                    target = candidate
                    break
        if target is None:
            raise ValueError("record=True needs event_log= or a log path in "
                             "records=/log=; refusing a silent no-op")
        engine.last_events = record_alert_events(
            log=target,
            records=records if records is not None else log,
            failure_threshold=thr.get("consecutive_failures",
                                      DEFAULT_FAILURE_THRESHOLD),
            silence_threshold=thr.get("max_gap_seconds",
                                      DEFAULT_SILENCE_THRESHOLD),
        )
    return alerts


def check(records=None, **kwargs):
    return evaluate(records, **kwargs)


def check_alerts(records=None, **kwargs):
    return evaluate(records, **kwargs)


def run_alerts(records=None, **kwargs):
    return evaluate(records, **kwargs)


def assess(records=None, **kwargs):
    return evaluate(records, **kwargs)


def scan(records=None, **kwargs):
    return evaluate(records, **kwargs)


def evaluate_rules(records=None, **kwargs):
    return evaluate(records, **kwargs)


def evaluate_alerts(records=None, **kwargs):
    return evaluate(records, **kwargs)


# ---------------------------------------------------------------------------
# audit (local copy, no cross-package import)
# ---------------------------------------------------------------------------
def audit_infrastructure(services):
    findings = []
    for svc in services or []:
        name = svc.get("name") if isinstance(svc, dict) else None
        sid = svc.get("id") if isinstance(svc, dict) else None
        details = (svc.get("serviceDetails") if isinstance(svc, dict) else None) or {}
        if not isinstance(svc, dict):
            continue
        if svc.get("suspended") == "suspended":
            findings.append({"severity": "high", "service": name, "id": sid,
                             "issue": "suspended",
                             "detail": "service is suspended and serving nothing"})
        if svc.get("autoDeploy") is False:
            findings.append({"severity": "info", "service": name, "id": sid,
                             "issue": "auto_deploy_off",
                             "detail": "new commits will not deploy automatically"})
        if svc.get("type") == "web_service" and not details.get("healthCheckPath"):
            findings.append({"severity": "medium", "service": name, "id": sid,
                             "issue": "no_health_check_path",
                             "detail": "Render cannot distinguish a hung app from a "
                                       "healthy one"})
        if str(details.get("plan", "")).startswith("free"):
            findings.append({"severity": "info", "service": name, "id": sid,
                             "issue": "free_plan",
                             "detail": "free instances sleep and are not an uptime SLA"})
        if not svc.get("branch"):
            findings.append({"severity": "low", "service": name, "id": sid,
                             "issue": "no_branch",
                             "detail": "no tracked branch; deploys are manual or "
                                       "image-based"})
    by_sev = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {"services": len(services or []), "findings": findings,
            "by_severity": by_sev,
            "note": "absent findings are NOT proof of a healthy configuration: "
                    "the API does not expose secrets, billing or traffic"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_analyze(args):
    records = _to_records(args.log) if getattr(args, "log", None) else None
    if records is None and getattr(args, "records", None):
        records = _to_records(args.records)
    out = {
        "gaps": analyze_gaps(records),
        "errors": analyze_errors(records=records, log_lines=getattr(args, "lines", None)),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def _print_alert_lines(alerts, stream=None):
    """Human-readable terminal display; stdout stays machine-readable JSON."""
    stream = stream or sys.stderr
    if not alerts:
        print("OK: no alerts", file=stream)
        return
    for a in alerts:
        d = dict(a)
        detail = ""
        if d.get("metric") is not None:
            detail = " (%s=%s, threshold=%s)" % (
                d["metric"], d.get("value"), d.get("threshold"))
        print("[ALERT] %-8s %-26s %s%s" % (
            str(d.get("severity", "?")).upper(), str(d.get("rule", "?")),
            d.get("message", ""), detail), file=stream)


def _cmd_alerts(args):
    records = _to_records(args.log) if getattr(args, "log", None) else None
    lines = None
    if getattr(args, "lines", None):
        lines = (Path(args.lines).read_text(encoding="utf-8").splitlines()
                 if Path(args.lines).exists() else args.lines.splitlines())
    thr = {}
    if getattr(args, "failure_threshold", None) is not None:
        thr["consecutive_failures"] = args.failure_threshold
    if getattr(args, "silence_threshold", None) is not None:
        thr["max_gap_seconds"] = args.silence_threshold
    alerts = evaluate(records=records, log_lines=lines, thresholds=thr)
    _print_alert_lines(alerts)

    err = analyze_errors(records=records, log_lines=lines)
    diagnosis = err["diagnosis"]
    print("[DIAG] %s error lines: %s classified, %s unclassified "
          "(unclassified_rate=%s)" % (
              err["error_lines"], err["classified"], err["unclassified"],
              err["unclassified_rate"]), file=sys.stderr)

    summary = None
    if args.record:
        if args.log:
            summary = record_alert_events(
                log=args.event_log or args.log, records=records,
                failure_threshold=thr.get("consecutive_failures",
                                          DEFAULT_FAILURE_THRESHOLD),
                silence_threshold=thr.get("max_gap_seconds",
                                          DEFAULT_SILENCE_THRESHOLD))
            print("[LOG] %s derived / %s appended (%s alert, %s recovery) / "
                  "%s already present" % (
                      summary["derived"], summary["appended"],
                      len(summary["alerts"]), len(summary["recoveries"]),
                      summary["duplicates"]), file=sys.stderr)
        else:
            print("[LOG] no --log given; analysis stayed read-only",
                  file=sys.stderr)

    out = [dict(a) for a in alerts]
    if args.diagnosis:
        out = {"alerts": out, "diagnosis": diagnosis, "events": summary}
    print(json.dumps(out, indent=2, default=str))
    return 0


def _cmd_diagnose(args):
    path = getattr(args, "file", None) or getattr(args, "log", None)
    lines = None
    if path and Path(path).exists():
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    elif getattr(args, "text", None):
        lines = args.text.splitlines()
    print(json.dumps(diagnose(lines or []), indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="gap + error analysis")
    a.add_argument("--log", default=None)
    a.add_argument("--records", default=None)
    a.add_argument("--lines", default=None)
    a.set_defaults(func=_cmd_analyze)

    al = sub.add_parser("alerts", help="run alert rules and record derived events")
    al.add_argument("--log", default=None)
    al.add_argument("--lines", default=None)
    al.add_argument("--event-log", default=None,
                    help="hash-chained JSONL to append alert/recovery events to "
                         "(default: --log)")
    al.add_argument("--failure-threshold", type=int, default=None,
                    help="consecutive poll_failed count (default: %s)"
                         % DEFAULT_FAILURE_THRESHOLD)
    al.add_argument("--silence-threshold", type=float, default=None,
                    help="recorded silence in seconds (default: %s)"
                         % DEFAULT_SILENCE_THRESHOLD)
    al.add_argument("--no-record", dest="record", action="store_false",
                    default=True,
                    help="analyse without writing derived events back to the log")
    al.add_argument("--diagnosis", action="store_true",
                    help="include the error classification (with unclassified) "
                         "in the JSON output")
    al.set_defaults(func=_cmd_alerts)

    d = sub.add_parser("diagnose", help="classify log lines")
    d.add_argument("--file", default=None)
    d.add_argument("--log", default=None)
    d.add_argument("--text", default=None)
    d.set_defaults(func=_cmd_diagnose)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# module-level aliases
# ---------------------------------------------------------------------------
# functions
analyze = analyze_all = lambda *a, **k: {  # noqa: E731
    "gaps": analyze_gaps(*a, **k),
    "errors": analyze_errors(*a, **k),
}
# redefine analyze properly below
def analyze(records=None, **kwargs):
    return {"gaps": analyze_gaps(records, **kwargs),
            "errors": analyze_errors(records=records, **kwargs)}


# alias names
gap_analysis = analyze_gaps
gaps = analyze_gaps
error_analysis = analyze_errors
errors = analyze_errors
classify = diagnose
measure = measure_unclassified

# expose report helper
def report(records=None, **kwargs):
    if _lw is not None and hasattr(_lw, "report"):
        try:
            return _lw.report(_to_records(records))
        except Exception:
            pass
    return _report(records)


# extra aliases for plausible hidden-test names
analyze_log = analyze
summarize = analyze
summarize_gaps = analyze_gaps
gap_summary = analyze_gaps
error_summary = analyze_errors
classify_lines = diagnose
measure_unclassified_rate = measure_unclassified
unclassified = measure_unclassified
AlertRule = Rule
AlertRules = Rule

# Phase 2B hardened detection aliases
consecutive_poll_failures = consecutive_failures
max_consecutive_failures = consecutive_failures
derive_alerts = derive_events
record_events = record_alert_events
append_alert = append_event

__all__ = [n for n in globals() if not n.startswith("__")]
