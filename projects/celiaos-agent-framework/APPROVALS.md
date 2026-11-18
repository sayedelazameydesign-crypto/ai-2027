# Local approval workflow (prototype)

The `verified_file` worker now consumes a matching, unexpired, single-use
approval immediately before invoking the real kernel. Callers pass an approval
ID and SHA-256 digest, not file parameters. Raw parameters and pending,
rejected, expired, changed or previously consumed requests are refused at this
worker boundary. A SQLite transaction claims the approval before execution;
concurrent claims permit only one worker. COMPLETED requires the kernel status,
independent hash verification and memory admission. A crash may leave CLAIMED,
and an exception produces UNKNOWN: inspect evidence rather than retrying.

`verified_demo.py` now creates a PENDING request only. `approved_demo.py` can
show, decide and execute requests. Tests simulate decisions; no real user has
authorized an action just by running the tests. The agent must obtain explicit
user approval for the exact displayed request before deciding APPROVED.

## Critical limitations

This is **not an authenticated approval channel** and not an OS security
boundary. Anyone with the same operating-system access can modify the database,
call `decide`, or invoke the underlying kernel directly. This guard applies only
to the new worker route, not every kernel entry point or browser operation. It
does not protect against a malicious agent with filesystem or shell access.
Approvals and results are plaintext in local SQLite; use only a private,
trusted runtime directory and never include secrets. Existing directories and
files are not automatically hardened. Durability across Arena environment
replacement has not been demonstrated.

Production requires a separately authenticated human approval service, protected
storage, separate execution identity, mandatory policy enforcement for all
kernel entry points, a tamper-resistant audit, crash recovery and security
review. There is no verified mechanism in this session that securely binds an
Arena user response to a decision in the database. Do not treat the local
`approve` command as proof of identity.

Browser control is still blocked and requires separately verified infrastructure
and independent consent for the extension and pairing.

Offline tests: `python projects/celiaos-agent-framework/test_approval_gate.py -v`
and `python projects/celiaos-agent-framework/test_verified_executor.py -v`.
