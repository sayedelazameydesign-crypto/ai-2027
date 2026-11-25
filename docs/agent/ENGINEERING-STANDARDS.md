# Engineering Standards — AI-2027

Standards for code written in this repository. These are enforced by CI gates
where a gate exists; where no gate exists, the standard still applies.

## 1. Layer discipline

Every file belongs to a layer, and the layer decides what it may import.

| Layer | Location | Allowed dependencies |
| --- | --- | --- |
| Kernel, memory, tools, deployment, adapters | `kernel-2027/`, `memory/`, `tools/`, `deployment/`, `browser_skill_adapter/`, `render_control_adapter/` | **Python stdlib only** |
| Protocol runtime | `python-2027/src/ai2027_protocol/` | Declared in `python-2027/pyproject.toml` |
| Projects / archives | `projects/` | Varies; archives are read-only by convention |

Rules:

1. **A stdlib-only layer must not import a third-party package.** CI enforces
   this with AST gates (`scripts/ci_guard.py imports` and the per-layer gates in
   `.github/workflows/ci.yml`). If a stdlib-only module needs a feature that
   would require a dependency, either implement it with the stdlib or move the
   feature to the protocol layer — do not add the import and weaken the gate.
2. **Do not add a dependency to the root `requirements.txt`.** The file is
   intentionally empty; its emptiness is a measured claim, and adding a line
   without a decision record breaks the project's dependency honesty.
3. **A new capability in a stdlib-only layer is stdlib-only.** If it needs
   network or a model client, it belongs in the protocol layer with a declared
   dependency, or it must degrade gracefully when the dependency is absent.

## 2. Python standards

### Style

- Python 3.11+ for the protocol package; the kernel and tooling must stay
  compatible with the oldest Python the CI matrix runs (3.10).
- Modules start with a docstring that states the **measured problem** the module
  solves, not just what it does. This repository's convention is to explain the
  failure the design prevents, in the module docstring.
- Use `from __future__ import annotations` in every new module.
- Prefer explicit types, `dataclass`/`Protocol` for contracts, and
  `frozen=True` for immutable records.
- Linting (`ruff`) and typing (`mypy --strict`) are enforced in
  `python-2027`. New modules there must pass both with no new violations.

### Error handling

- **Fail-closed.** Anything security-relevant (approval, sandbox, budget,
  credentials) defaults to denied when it cannot be proven.
- **Never silently swallow.** An exception that is caught must be recorded with
  enough context to reconstruct the failure.
- **Never upgrade a failure.** `DENIED`, `FAILED` and `UNVERIFIED` stay as they
  are. A run that had a failure is reported as a run that had a failure.
- **Fail-soft where the failure is a data condition**, but record the reason:
  a check that could not run is `passed=False` with the reason, never skipped.

### Security

- Secrets are redacted before they reach logs, prompts, reports or memory.
  `redact_secrets` in the protocol package is the reference implementation.
- An API key never implies a free tier, and never implies authorisation.
- Writes outside the workspace sandbox require an explicit approval decision.
- Path resolution uses the repo's safe-path resolver; never construct paths from
  unvalidated user input and join them naively.

## 3. Testing standards

1. **A change ships with a test.** No exceptions for "small" changes — the
   smallest change is exactly the one whose regression is invisible.
2. **Tests are hermetic.** No network, no live provider, no credential, no
   external service, unless the test is explicitly a live probe gated by its own
   workflow.
3. **Tests that assert a gate must be shown to fail.** A guard that has never
   been demonstrated to catch a real case is decoration. This is why this repo
   has self-tests for its own gates (`scripts/test_ci_guard.py`,
   `scripts/test_workflow_pipe_guard.py`).
4. **Run tests from the repository root.** A test that only passes from its own
   directory is a latent failure; CI runs suites from the root.
5. **Dependency-free tests stay dependency-free.** Root-level `tests/` runs with
   the standard library only (`python -m unittest`), so tests there must not
   import `pydantic`, `pytest` or any package that is not installed by default.
6. **Coverage is measured, not assumed.** `python-2027` enforces a coverage
   floor; a new module must be exercised by the package's own tests, not only
   by tests elsewhere in the tree.
7. **Mocks are labelled.** A test that exercises a mock provider must say so in
   its name or docstring, so a mock result cannot be misread as live evidence.

## 4. Prompt and configuration standards

- `prompts/agent/*.md` carry YAML-style front matter: `name`, `version`,
  `role`, `required` (the headings the file must contain). The loader validates
  all four; a file that fails validation is a build failure, not a warning.
- Prompt files describe **behaviour**, not lore. Each one states what it
  decides, what it must not do, and what output it produces.
- Version numbers are bumped when behaviour changes. Do not bump for
  rewording alone unless the wording changes a decision rule.
- `config/routing_profiles.yaml` declares **capabilities**, not model names.
  Profiles reference provider kinds and capability tags; concrete model IDs
  live in the provider catalogue and are resolved at runtime.
- A routing profile must be free-first: `max_spend_usd` is `0.0` unless an
  operator has explicitly authorised spend, and that authorisation is recorded
  in the profile's own notes.

## 5. Documentation standards

- **Docs are claims.** If a doc says a capability exists, the capability exists
  and is tested. Where a capability is declared but not implemented, the doc
  says so explicitly.
- A new architectural surface gets a document that maps it to the existing
  components, so a reader can see what is new and what is reused.
- Measured numbers are recorded with the date and the conditions of the
  measurement. A number without its conditions is not reproducible.

## 6. Review standards

A change is reviewable when:

- the diff is one coherent change;
- every new path is covered by a test that runs in CI;
- the change states what it did **not** change, especially anything it
  deliberately left untouched (the kernel, the budget policy, deployment);
- any simulated or offline result is labelled as such in the change description;
- no hard boundary was weakened to make the change pass.

A PR that cannot be verified by its own tests is not ready for review,
regardless of how complete the implementation looks.
