"""Per-user file locations. HOME is read at call time so tests can point it elsewhere."""
from pathlib import Path

HOME = Path.home()


def cache_file():
    return HOME / ".cache" / "devprune" / "last.json"


def config_file():
    return HOME / ".config" / "devprune" / "config.json"
