# Contributing

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e . pytest ruff
.venv/bin/pytest
.venv/bin/ruff check .
```

The project uses only the Python standard library at runtime (Python 3.9+). Please keep it that way.

## Adding a framework (no code needed)

Project rules live in `src/devprune/data/project_rules.json`. Rules are checked in order and the first match wins,
so put more specific rules (Next.js) before general ones (Node.js).

```json
{
  "framework": "Rust",
  "detect": {"files_all": ["Cargo.toml"]},
  "prune_dirs": ["src"],
  "targets": [
    {"path": "target", "label": "target/", "risk": "safe", "note": "Run `cargo build` again."}
  ]
}
```

- `detect.files_all`: files that must all exist. `detect.any_of`: at least one of `{"file": ...}`, `{"file_prefix": ...}`, `{"package_json_contains": ...}`.
- `prune_dirs`: folders the scanner should not descend into.
- Target `mode`: `dir` (default, delete the folder) or `contents` (empty it, keep the folder).
- `risk`: `safe` (recreated automatically), `medium` (needs a command like `npm install`), `careful` (may lose data).
- Paths are relative to the project, with no `..` and no absolute paths.

You can try a rule without changing the code by putting it under `project_rules` in
`~/.config/devprune/config.json`. Rules from config cannot run commands.

Global caches (`global_rules.json`) may use `cmd` to call an official cleanup tool. The command must be an exact list.
Set `fallback_delete` to `true` to delete the folder when the tool is missing, or `false` to hide the item.

## Safety rules for changes

Anything that decides *what may be deleted* (`safety.py`, `clean.py`, cache loading) needs a test, including a negative
test showing that the unsafe case is refused. Tests run against a fake `$HOME` and never touch real files.

## Pull requests

Run `pytest` and `ruff check .` first. Describe what you changed and how you tested it.

## Releasing (maintainer)

Releases go to PyPI with [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): GitHub proves to PyPI who it
is, so there is no API token to store or leak. Nothing is published automatically. `.github/workflows/publish.yml`
only runs when the owner starts it by hand, and the `pypi` environment also needs the owner's approval and a `v*` tag.

**One-time setup** (on both <https://test.pypi.org> and <https://pypi.org>, with 2FA on): *Account settings → Publishing →
Add a pending publisher*, with

| Field | TestPyPI | PyPI |
|---|---|---|
| PyPI project name | `devprune` | `devprune` |
| Owner | `dhikicheeks` | `dhikicheeks` |
| Repository name | `devprune` | `devprune` |
| Workflow name | `publish.yml` | `publish.yml` |
| Environment name | `testpypi` | `pypi` |

**Each release**

1. Bump `version` in `pyproject.toml` and `__version__` in `src/devprune/__init__.py`, date the `CHANGELOG.md` entry,
   and merge once CI is green. A version number can never be reused on PyPI.
2. Tag it (`git tag -a v0.1.1 -m "DevPrune 0.1.1"`, then `git push origin v0.1.1`) and create the GitHub Release.
3. *Actions → Publish to PyPI → Run workflow*: first with target `testpypi`, check
   <https://test.pypi.org/project/devprune/>, then run it again from the tag with target `pypi` and approve it.
4. Check that `pipx install devprune` works in a clean environment.
