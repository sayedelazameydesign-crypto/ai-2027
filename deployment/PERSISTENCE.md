# Live Agent persistence and recovery design (Phase 2)

**Status: design + local tooling only.** No Render, GitHub Pages, GitHub Actions,
container, or hosting settings were changed. Do not treat this document as approval
to deploy or move production data. The owner must select the storage provider,
retention, recovery objectives, encryption/KMS, and cutover process separately.

## What must be durable

The canonical Live Agent runtime contains three logical roots:

- `runs/`: persisted DecisionRecords and append-only `runs/ledgers/` evidence.
- `sandboxes/`: per-run artifacts referenced by records and memory.
- `state/memory.db`: SQLite evidence memory (plus transient SQLite WAL/SHM files
  while the service is active).

A record is not useful by itself if its artifact or ledger is missing. Back up and
restore these roots as one runtime generation. `AI2027_LIVE_RUNTIME_DIR` selects a
runtime root, but configuring a path does **not** make its filesystem durable. The
offline snapshot tool also recognizes the older layout with `memory.db` at the
runtime root and normalizes it to `state/memory.db` when restored. If both legacy and
canonical databases exist, it refuses to guess which one is authoritative.
Process restart on the same retained filesystem is the only current service-level
persistence guarantee. Instance replacement, redeploy, disk loss, and regional
failure are not covered by the current hosting blueprint.

## Proposed production topology

1. Keep GitHub Pages as the public static UI, with no secrets embedded in its HTML.
2. Put a trusted HTTPS gateway in front of the Live Agent API. The gateway owns
   user sessions, per-user authorization, CSRF/origin policy where relevant,
   distributed rate limits, and credential rotation. The service's interim
   `AI2027_READ_TOKEN_HASHES` / `AI2027_WRITE_TOKEN` controls are not a final
   multi-user identity system.
3. Mount a private, access-controlled persistent volume for the runtime. Only the
   service account and approved recovery operators should read it. Do not enable
   public object listing or unauthenticated artifact access.
4. Keep encrypted, versioned backups in a separate failure domain/account with
   least-privilege write/read roles, retention, access audit, and deletion
   protection. Select a provider-managed KMS or an approved client-side encryption
   process; this repository does not choose a provider or hold encryption keys.
5. Choose and document owner-approved RPO/RTO before production. Periodically
   restore to an isolated staging volume and verify records, artifacts, ledger
   consistency, and SQLite before approving any cutover.

A persistent mount and an independent backup solve different failure modes. Docker
is only packaging and does not replace a persistent-storage/backup design.

## Consistent backup procedure

A backup must represent one coherent generation across records, sandboxes, ledgers,
and memory. The current tool is deliberately offline: **stop the service or otherwise
quiesce all writes first**, then run the snapshot. Do not copy a live runtime tree
with ordinary `cp`/`tar`; an in-flight run could be captured between record, artifact,
ledger, or memory writes. SQLite is captured with its backup API, but that alone does
not make the other roots a cross-component transaction.

```bash
python3 deployment/runtime_snapshot.py backup \
  /path/to/quiesced/live-agent-runtime \
  /path/to/private-staging/live-agent-UTC.tar.gz
```

The tool includes only `runs/`, `sandboxes/`, and `state/` (plus a legacy root-level
`memory.db`, normalized into `state/`); rejects symbolic links, special files, and
non-portable Windows path components (drive/ADS syntax, reserved device names,
invalid characters/control codes, trailing dots/spaces, overlong components, and
case-insensitive path collisions), including unsafe SQLite/WAL/SHM/journal sidecars;
captures SQLite consistently; writes a
manifest of relative file names, sizes, and SHA-256 digests; and publishes without
overwriting an existing archive. On POSIX, restored directories are owner-only
(`0700`) and files are owner-only (`0600`). The manifest may disclose user filenames,
and archives contain all run content. **They are not encrypted or signed.** Encrypt
and store them under the approved
KMS/object-store policy before it leaves a trusted host. Checksums detect accidental
corruption, not a malicious party who can replace both data and manifest. Add an
independent signature/MAC if the selected backup system does not provide authenticated
versioning.

## Restore and cutover procedure

Restore only into a new, isolated directory under a parent controlled by the recovery
operator. The restore tool refuses existing destinations, validates archive member
paths/types and the manifest before publishing, verifies each file hash and SQLite
`quick_check`, and does not follow archive symlinks or path traversal.

```bash
python3 deployment/runtime_snapshot.py restore \
  /path/to/verified/live-agent-UTC.tar.gz \
  /path/to/isolated-parent/live-agent-restore
```

After restore, perform application-level checks (read restored records and artifacts,
check a representative ledger, verify SQLite/memory counts, and run regression tests)
while the service is still isolated. A production cutover requires separate owner
approval: point the approved runtime mount/gateway to the verified restore, monitor
health and cleanup outcomes, and retain the previous volume until the recovery is
accepted. Never use `--fresh` as a recovery tool; it intentionally deletes the
selected runtime. Never overwrite the only surviving copy.

The utility does not stop a service, encrypt/upload backups, provision storage,
choose retention, or switch traffic. Those are operator/platform responsibilities
and remain unconfigured in this phase.

## Verification

```bash
python3 deployment/test_runtime_snapshot.py -v
python3 scripts/test_runtime_restart.py -v
```

The snapshot test creates a synthetic run, record, sandbox artifact, ledger, and
SQLite memory in a temporary runtime; it backs them up, restores into a second
isolated temporary directory, then asks the Live Agent code to reload and verify the
record, artifact, ledger file, and memory. Malformed traversal paths, checksum
mismatches, symbolic-link sources, and existing restore targets are also rejected.
No real user data or hosting credentials are used.
