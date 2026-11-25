"""Persist the last scan so the dashboard opens instantly. The cache file is never trusted blindly."""
import json
import time
from pathlib import Path

from . import paths, safety, state, trash

CACHE_VERSION = 3
PUB = ("id", "framework", "group", "label", "path", "note", "risk", "kb")


def pub(c):
    """Item as shown to the UI. Items whose path vanished are reported with size 0."""
    exists = Path(c["path"]).exists()
    d = {k: c[k] for k in PUB} | {"has_cmd": bool(c["cmd"]), "exists": exists,
                                  "can_stage": not c["cmd"] and trash.can_stage(c["path"])}
    if not exists:
        d["kb"] = 0
    return d


def save_cache():
    try:
        f = paths.cache_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with state.LOCK:
            items = list(state.STATE["items"].values())
        state.CACHE_TS[0] = time.time()
        f.write_text(json.dumps({"v": CACHE_VERSION, "ts": state.CACHE_TS[0], "items": items}))
    except OSError:
        pass


def load_cache():
    """Load cached items, dropping any that fail today's safety checks."""
    try:
        d = json.loads(paths.cache_file().read_text())
        if d.get("v") != CACHE_VERSION:
            return
        allowed = safety.allowed_cmds()
        ok = {}
        for i in d["items"]:
            if not isinstance(i.get("path"), str) or not safety.is_safe(Path(i["path"])):
                continue
            if i.get("cmd") and tuple(i["cmd"]) not in allowed:
                continue
            if i.get("mode") not in ("dir", "contents") or i.get("guard") not in safety.ALLOWED_GUARDS:
                continue
            ok[i["id"]] = i
        state.STATE["items"] = ok
        state.CACHE_TS[0] = d.get("ts")
    except (OSError, ValueError, KeyError, TypeError):
        pass
