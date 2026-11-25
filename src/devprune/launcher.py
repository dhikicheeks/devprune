"""Create short commands that start the dashboard (e.g. `bersih`), managed from the UI.

Launchers are tiny shell scripts in ~/.local/bin that carry a marker line. Only files with that marker are ever
listed, replaced or removed, and a name that already resolves to some other program is refused.
"""
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from . import paths

MARKER = "# devprune launcher (created from the dashboard)"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED = {"cd", "alias", "echo", "exit", "export", "source", "test", "type", "which", "command", "eval",
            "exec", "set", "unset", "read", "sudo", "rm", "sh", "bash", "zsh"}
MAX_LAUNCHERS = 5


class LauncherError(Exception):
    pass


def bin_dir():
    return paths.HOME / ".local" / "bin"


def _is_ours(p):
    try:
        if p.is_symlink() or not p.is_file():
            return False
        with open(p, "r", encoding="utf-8") as f:
            f.readline()
            return f.readline().strip() == MARKER
    except (OSError, UnicodeDecodeError):
        return False


def _validate(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise LauncherError("Use 1-32 characters: lowercase letters, digits, - and _ (start with a letter).")
    if name in RESERVED:
        raise LauncherError(f"'{name}' is a reserved name.")
    return bin_dir() / name


def _script():
    pkg_parent = Path(__file__).resolve().parent.parent
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        f"export PYTHONPATH={shlex.quote(str(pkg_parent))}\"${{PYTHONPATH:+:$PYTHONPATH}}\"\n"
        f"exec {shlex.quote(sys.executable)} -m devprune \"$@\"\n"
    )


def installed():
    d = bin_dir()
    try:
        names = sorted(p.name for p in d.iterdir() if NAME_RE.match(p.name) and _is_ours(p))
    except OSError:
        return []
    return names


def on_path():
    d = bin_dir()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        try:
            if entry and Path(entry).expanduser().resolve() == d.resolve():
                return True
        except OSError:
            pass
    return False


def status():
    return {"dir": "~/.local/bin", "on_path": on_path(), "commands": installed()}


def install(name):
    target = _validate(name)
    if _is_ours(target):
        pass  # refreshing our own launcher is fine
    else:
        if target.exists() or target.is_symlink():
            raise LauncherError(f"'{name}' already exists in ~/.local/bin and was not created by this app.")
        if shutil.which(name):
            raise LauncherError(f"'{name}' is already a command on this system. Pick another name.")
        if len(installed()) >= MAX_LAUNCHERS:
            raise LauncherError(f"At most {MAX_LAUNCHERS} commands. Remove one first.")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{name}.tmp")
    tmp.write_text(_script(), encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, target)
    return status()


def remove(name):
    target = _validate(name)
    if not _is_ours(target):
        raise LauncherError(f"'{name}' was not created by this app, so it is left alone.")
    target.unlink()
    return status()
