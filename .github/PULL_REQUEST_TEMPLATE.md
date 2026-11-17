## What changed

<!-- One paragraph. What is different after this PR, for a user? -->

## Evidence (required)

Paste the command you ran and its output. A claim without a command is not a
review item in this repository.

```
$ <command>
<output>
```

- [ ] I ran the tests that cover this change locally
- [ ] I ran `python3 scripts/runtime_smoke.py` if the runtime was touched
- [ ] I updated any published number this change invalidates (README, docs, memory/README.md)
- [ ] No secrets, tokens or private data are included

## Honest limits

<!-- What does this NOT do or fix? What still fails? -->

## Risk

- [ ] Reversible in one commit
- [ ] Touches the evidence path (`core/`, `memory/`, `scripts/`)
- [ ] Touches CI, release or deployment configuration
