# CeliaOS: a narrow real execution bridge

`main.py` remains a simulation: its Python executor returns a placeholder. The
separate verified-file worker runs the existing agi-system kernel and
 evidence-gated memory in a separate Python process (the projects have conflicting
`core` and `memory` package names). It supports only one local file operation;
there is no browser, network, arbitrary shell, or LLM in this bridge.

The worker requires a matching, unexpired single-use approval before invoking
the kernel. `verified_demo.py` now creates a PENDING request; it does not execute
it. For the review and security limitations see [APPROVALS.md](APPROVALS.md).
This local approval flow is **not authenticated** and is not a security boundary
against code with the same operating-system access.

The action creates a file in a kernel-managed per-run sandbox. The result
includes kernel status, memory admission, artifact SHA-256 and an independently
computed hash comparison. Evidence and SQLite data remain in the specified
runtime directory. Do not put secrets in the content: both records and files
are stored in plaintext. No persistence after an Arena restart or replacement is
claimed. The old simulation workflow is deliberately not connected: it reports
completion without real execution or verification.

Run the offline tests from the repository root:

```sh
python projects/celiaos-agent-framework/test_verified_executor.py -v
python projects/celiaos-agent-framework/test_approval_gate.py -v
```
