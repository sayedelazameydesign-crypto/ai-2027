# Licensing notes

The repository is licensed **MIT** — the full text is in [`LICENSE`](../LICENSE).
`LICENSE` is kept byte-clean (no appended commentary) because GitHub's license
detector reads the whole file: an extra note after the MIT body made the API report
`NOASSERTION` instead of `MIT`. Measured, then fixed.

## Scope

Scope note (not a legal opinion): this license covers the repository's own
code. A subdirectory that carries its own LICENSE file is governed by that
file instead. Third-party names (GitHub, Vercel, Supabase, Notion, ...) are
used descriptively and grant no rights to their marks.

Why MIT: the code is a reference implementation meant to be read, forked and
re-run. Apache-2.0 would add an explicit patent grant — change this file in one
commit if you prefer that (or replace it with a proprietary notice).

## Changing the license

Replace `LICENSE` in one commit and update `CHANGELOG.md`. If you choose
Apache-2.0, add the `NOTICE` file it requires.
