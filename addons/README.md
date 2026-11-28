# addons/ — Unified Add-on Collection Protocol v1

One folder per add-on; the folder name **is** the add-on `id`.

```
addons/<addon_id>/addon.json   # id, name, version (semver), entrypoint (+ optional fields below)
addons/<addon_id>/<entrypoint> # must exist inside the folder
addons/<addon_id>/assets/...   # optional
```

| Field | Required | Rule |
|---|---|---|
| `id` | yes | `^[a-z0-9_]{3,64}$` and equal to the folder name |
| `name` | yes | non-empty string |
| `version` | yes | semantic version, e.g. `1.0.0` |
| `entrypoint` | yes | relative `/` path to a file inside the folder (no `..`, no absolute paths) |
| `description` | no | string |
| `dependencies` | no | list of **add-on ids**; missing / inactive / cyclic → `degraded` (transitive) |
| `requirements` | no | list of external package specs — recorded only, never installed |
| `permissions` | no | subset of `read_fs`, `network`, `ui_hook` — anything else → `rejected` |

Unknown fields, symlinks anywhere in the folder, broken JSON or a manifest over 64 KB → `rejected`.
One bad add-on never stops the run.

```bash
python3 collector/protocol_engine.py          # rebuild registry/registry.json (+ chained audit entry if it changed)
python3 collector/protocol_engine.py --check  # CI: exit 1 if any add-on changed after indexing
python3 -m unittest tests.test_collector -v
```

The collector never imports or runs add-on code, and `permissions` are a declaration:
enforcement belongs to the runtime that loads the add-on.
