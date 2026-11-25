"""Optional user config: ~/.config/devprune/config.json

{ "roots": ["~/Developer"], "exclude": ["~/Developer/keep-me"], "project_rules": [ ... ] }
"""
import json
import sys
from pathlib import Path

from . import paths, rules, safety

DEFAULT_ROOT_NAMES = [
    "Projects", "Developer", "Code", "Sites", "www", "htdocs", "Documents", "Desktop", "Herd", "Valet",
]
CURRENT = {"roots": [], "exclude": [], "project_rules": []}


def _expand(p):
    s = str(p)
    return paths.HOME / s[2:] if s.startswith("~/") else Path(s)


def _warn(msg):
    print(f"devprune: config: {msg}", file=sys.stderr)


def load():
    """Read config.json into CURRENT and apply exclusions. Bad config is reported and ignored."""
    cfg = {"roots": [], "exclude": [], "project_rules": []}
    try:
        raw = json.loads(paths.config_file().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:
        _warn(f"ignoring unreadable config ({e})")
        raw = {}
    for key in ("roots", "exclude"):
        val = raw.get(key, [])
        if isinstance(val, list):
            cfg[key] = [str(_expand(v)) for v in val if isinstance(v, str)]
        else:
            _warn(f"'{key}' must be a list; ignored")
    for i, r in enumerate(raw.get("project_rules", []) if isinstance(raw.get("project_rules", []), list) else []):
        try:
            cfg["project_rules"].append(rules.validate_project_rule(r))
        except rules.RuleError as e:
            _warn(f"project_rules[{i}] ignored: {e}")
    CURRENT.update(cfg)
    safety.EXCLUDE[:] = [_resolve(p) for p in cfg["exclude"]]
    return CURRENT


def _resolve(p):
    try:
        return Path(p).resolve()
    except (OSError, RuntimeError):
        return Path(p)


def default_roots():
    if CURRENT["roots"]:
        return list(CURRENT["roots"])
    return [str(paths.HOME / n) for n in DEFAULT_ROOT_NAMES if (paths.HOME / n).is_dir()]
