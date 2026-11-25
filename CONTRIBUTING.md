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
