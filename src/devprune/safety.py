"""Path safety. Everything that deletes goes through is_safe()."""
from pathlib import Path

from . import paths, rules

# Direct children of $HOME that may be cleanup targets (everything else at depth 1 is refused).
ALLOWED_HOME_CHILDREN = {".pub-cache", ".Trash"}
# Never touched, at any depth.
SENSITIVE = [(".ssh",), (".gnupg",), (".aws",), (".kube",), (".config", "gcloud"),
             ("Library", "Keychains"), ("Library", "Mail"), ("Library", "Messages"),
             ("Library", "Application Support")]
ALLOWED_GUARDS = (None, "gradle")
EXCLUDE = []  # resolved paths the user asked never to touch (config.json "exclude")


def allowed_cmds():
    return rules.builtin_commands()


def is_safe(p):
    """True only if p is inside $HOME, is not $HOME/root, a sensitive folder, a top-level user folder
    (Documents, Desktop, Library, ...) or related to a user-excluded path."""
    try:
        rp = Path(p).resolve()
        home = paths.HOME.resolve()
        if rp in (Path("/"), home) or not rp.is_relative_to(home):
            return False
        rel = rp.relative_to(home).parts
    except (OSError, RuntimeError, ValueError):
        return False
    if len(rel) == 1 and rel[0] not in ALLOWED_HOME_CHILDREN:
        return False
    if any(rel[:len(s)] == s for s in SENSITIVE):
        return False
    if is_excluded(rp):
        return False
    return True


def inside_excluded(rp):
    """True if rp is an excluded path or lies inside one (used to skip it while scanning)."""
    return any(rp == e or rp.is_relative_to(e) for e in EXCLUDE)


def is_excluded(rp):
    """True if rp is inside an excluded path, or contains one (deleting it would delete the excluded path)."""
    return any(inside_excluded(rp) or e.is_relative_to(rp) for e in EXCLUDE)
