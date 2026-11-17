# Phase 2B — smart alerting, gap analysis and error analysis

**Status:** built. Stdlib only.

Module: `monitoring/phase2b.py` (aliases: `alerting`, `alerts`, `gap_analysis`,
`error_analysis`, `gaps`, `errors`, ...).

- `analyze_gaps(records)` — structured summary of silences, poll failures,
  transitions, observed fraction.
- `analyze_errors(...)` / `diagnose(lines)` — classify log lines and report the
  unclassified rate instead of guessing.
- `AlertEngine` — rule-based alerting over the above; default rules cover
  repeated `poll_failed`, large gaps, low observed fraction, unclassified errors.
- `build_context`, `evaluate`, `check_alerts`, ...

CLI:

```bash
python3 monitoring/phase2b.py analyze --log watch.jsonl
python3 monitoring/phase2b.py alerts --log watch.jsonl
python3 monitoring/phase2b.py diagnose --file app.log
```
