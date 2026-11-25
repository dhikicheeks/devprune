"""Cleanup rules as data. Built-in rules live in data/*.json; users may add project rules in config.json.

Project rule:   {"framework", "detect", "targets": [{"path", "label"?, "mode"?, "risk"?, "note"?}], "prune_dirs"?}
  detect:       {"files_all": [names], "any_of": [{"file"|"file_prefix"|"package_json_contains": str}]}
  Frameworks are matched in order; the first match names the project, every match contributes targets.
Global rule:    built-in only (they may run tool commands such as `pnpm store prune`).
"""
import json
from pathlib import Path, PurePosixPath

DATA_DIR = Path(__file__).parent / "data"
RISKS = ("safe", "medium", "careful")
MODES = ("dir", "contents")
_CACHE = {}


ANY_OF_KEYS = ("file", "file_prefix", "package_json_contains")


class RuleError(ValueError):
    pass


def _read(name):
    if name not in _CACHE:
        _CACHE[name] = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    return _CACHE[name]


def global_rules():
    return _read("global_rules.json")


def builtin_commands():
    """Every command the built-in rules may run; nothing else is ever executed."""
    return {tuple(r["cmd"]) for r in global_rules() if r.get("cmd")}


def _check_relpath(path):
    if not isinstance(path, str) or not path:
        raise RuleError("target path must be a non-empty string")
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise RuleError(f"target path must be relative and stay inside the project: {path!r}")


def validate_project_rule(rule):
    """Return a normalized copy of a project rule or raise RuleError."""
    if not isinstance(rule, dict):
        raise RuleError("rule must be an object")
    if "cmd" in rule or any("cmd" in t for t in rule.get("targets", []) if isinstance(t, dict)):
        raise RuleError("project rules cannot run commands")
    fw = rule.get("framework")
    if not isinstance(fw, str) or not fw.strip():
        raise RuleError("'framework' is required")
    det = rule.get("detect")
    if not isinstance(det, dict) or not (det.get("files_all") or det.get("any_of")):
        raise RuleError("'detect' needs files_all and/or any_of")
    if set(det) - {"files_all", "any_of"}:
        raise RuleError("unknown key in 'detect'")
    files_all = det.get("files_all", [])
    if not isinstance(files_all, list) or not all(isinstance(f, str) for f in files_all):
        raise RuleError("files_all must be a list of file names")
    any_of = det.get("any_of")
    if any_of is not None:
        if not isinstance(any_of, list) or not any_of:
            raise RuleError("any_of must be a non-empty list")
        for c in any_of:
            if not isinstance(c, dict) or len(c) != 1 or next(iter(c)) not in ANY_OF_KEYS:
                raise RuleError("each any_of entry needs exactly one of file, file_prefix, package_json_contains")
            if not isinstance(next(iter(c.values())), str):
                raise RuleError("any_of values must be strings")
    targets = rule.get("targets")
    if not isinstance(targets, list) or not targets:
        raise RuleError("'targets' must be a non-empty list")
    norm = []
    for t in targets:
        if not isinstance(t, dict):
            raise RuleError("each target must be an object")
        _check_relpath(t.get("path"))
        mode, risk = t.get("mode", "dir"), t.get("risk", "medium")
        if mode not in MODES:
            raise RuleError(f"mode must be one of {MODES}")
        if risk not in RISKS:
            raise RuleError(f"risk must be one of {RISKS}")
        norm.append({"path": t["path"], "label": str(t.get("label") or t["path"]), "mode": mode,
                     "risk": risk, "note": str(t.get("note", ""))})
    prune = rule.get("prune_dirs", [])
    if not isinstance(prune, list) or not all(isinstance(x, str) and "/" not in x for x in prune):
        raise RuleError("prune_dirs must be a list of folder names")
    return {"framework": fw.strip(), "detect": det, "targets": norm, "prune_dirs": prune}


def project_rules(user_rules=()):
    """Built-in rules first (they take precedence for naming), then validated user rules."""
    rules = [validate_project_rule(r) for r in _read("project_rules.json")]
    return rules + list(user_rules)


def matches(rule, files, package_json_text=""):
    det = rule["detect"]
    if any(f not in files for f in det.get("files_all", [])):
        return False
    any_of = det.get("any_of")
    if any_of is None:
        return True
    for c in any_of:
        if "file" in c and c["file"] in files:
            return True
        if "file_prefix" in c and any(f.startswith(c["file_prefix"]) for f in files):
            return True
        if "package_json_contains" in c and c["package_json_contains"] in package_json_text:
            return True
    return False
