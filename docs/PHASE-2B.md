# Phase 2B — smart alerting, gap analysis and error analysis

**Status:** built. Stdlib only. No remote notifications.

Module: `monitoring/phase2b.py` (aliases: `alerting`, `alerts`, `gap_analysis`,
`error_analysis`, `gaps`, `errors`, ...). The detailed reference — detection
semantics, hash-chained `alert`/`recovery` events, CLI and limits — is
[PHASE-2B-ALERTING.md](PHASE-2B-ALERTING.md).

- `analyze_gaps(records)` — structured summary of silences, poll failures,
  transitions, observed fraction, plus `consecutive_failures` (adjacent runs).
- `analyze_errors(...)` / `diagnose(lines)` — classify log lines and report the
  `unclassified` count and rate instead of guessing.
- `AlertEngine` — rule-based alerting over the above. Defaults: **3 consecutive**
  `poll_failed` records, a **recorded 300 s** silence, low observed fraction,
  high unclassified rate. Rules are data and can be listed/overridden.
- `derive_events` / `append_event` / `record_alert_events` — deterministically
  write `alert` and `recovery` milestones into the Phase 2A hash-chained JSONL
  through `EventLog.append` (never a raw write); re-analysis is idempotent.
- `build_context`, `evaluate`, `check_alerts`, ...

CLI:

```bash
python3 monitoring/phase2b.py analyze  --log watch.jsonl
python3 monitoring/phase2b.py alerts   --log watch.jsonl            # + terminal display + recorded events
python3 monitoring/phase2b.py alerts   --log watch.jsonl --no-record --diagnosis
python3 monitoring/phase2b.py diagnose --file app.log
python3 monitoring/test_phase2b.py -v  # 9 tests
```

`scripts/generate_launch_evidence.py --assert` now gates on both monitoring
suites (`watchdog` 44 + `phase2b` 9). Timings and red-team counts in that pack
are measured per run, never quoted as constants.
