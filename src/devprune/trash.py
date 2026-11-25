"""Staging area: move items aside with rename, then empty or restore them later (D-19).

Layout:  <HOME>/.cache/devprune/trash/<run-id>/manifest.json
                                    /<NNN>/<original-name>      (mode "dir")
                                    /<NNN>/<child>, <child>...  (mode "contents")

The manifest is a journal. An entry is written as "moving" before the rename and flipped to "staged"
after it, so reconcile() can repair whatever a crash left behind. The manifest lives in a user-writable
folder, so it is treated as untrusted data (like the scan cache): nothing in it is used for restore
unless it passes the same safety checks as a delete target.
"""
import errno
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from . import paths, safety

MANIFEST = "manifest.json"
VERSION = 1
RUN_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
ENTRY_RE = re.compile(r"^\d{3,5}$")
MODES = ("dir", "contents")
STATUSES = ("moving", "staged")
_LOCK = threading.RLock()
# Locations (relative to HOME) that are emptied, never moved aside: moving the macOS Trash into a staging
# folder would be pointless and confusing. Enforced here in code, so config or cache cannot bypass it.
NEVER_STAGE = ((".Trash",),)


class TrashError(Exception):
    """A move/restore/empty that could not be done; the message is shown to the user."""


# ---------------------------------------------------------------- paths and manifest I/O

def root():
    return paths.trash_root()


def ensure_root():
    """Create the staging root (0700) and make sure it is a real directory inside HOME."""
    r = root()
    try:
        r.mkdir(parents=True, exist_ok=True)
        if r.is_symlink() or not r.is_dir():
            raise TrashError("The DevPrune staging folder is not a plain directory.")
        if not r.resolve().is_relative_to(paths.HOME.resolve()):
            raise TrashError("The DevPrune staging folder is outside your home folder.")
        os.chmod(r, 0o700)
    except OSError as e:
        raise TrashError(f"Cannot use the DevPrune staging folder: {e.strerror or e}") from e
    return r


def _inside_root(p):
    try:
        return Path(p).resolve().is_relative_to(root().resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def _save(run):
    """Write the manifest atomically: temp file, fsync, os.replace."""
    data = {"v": VERSION, "created": run["created"], "entries": run["entries"]}
    tmp = run["dir"] / (MANIFEST + ".tmp")
    final = run["dir"] / MANIFEST
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except OSError as e:
        raise TrashError(f"Cannot write the staging manifest: {e.strerror or e}") from e


def create_run():
    """Start a new run (one per clean). The run folder and manifest are created immediately."""
    ensure_root()
    for _ in range(5):
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        d = root() / rid
        try:
            d.mkdir()
        except FileExistsError:
            continue
        except OSError as e:
            raise TrashError(f"Cannot create a staging run: {e.strerror or e}") from e
        run = {"id": rid, "dir": d, "created": time.time(), "entries": {}, "orphan": False}
        _save(run)
        return run
    raise TrashError("Cannot create a staging run.")


# ---------------------------------------------------------------- staging

def _clean_name(name):
    return isinstance(name, str) and name not in ("", ".", "..") and "/" not in name and "\0" not in name


def can_stage(path):
    """False for locations that must be deleted for real (see NEVER_STAGE) or that cannot be resolved."""
    try:
        rel = Path(path).resolve().relative_to(paths.HOME.resolve()).parts
    except (OSError, RuntimeError, ValueError):
        return False
    return not any(rel[:len(n)] == n for n in NEVER_STAGE)


def close_run(run):
    """Call when a clean is finished: a run that ended up holding nothing is removed."""
    with _LOCK:
        _drop_if_empty(run)


def stage(run, item, src):
    """Move `src` (an item from the scan) into the run. Returns the manifest entry.

    Raises TrashError when the item cannot be moved; the source is left untouched in that case.
    """
    with _LOCK:
        src = Path(src)
        if src.is_symlink():
            raise TrashError("Refused: path is a symlink.")
        if not safety.is_safe(src):
            raise TrashError("Refused: unsafe path.")
        if _inside_root(src):
            raise TrashError("Refused: path is inside the DevPrune staging folder.")
        if not can_stage(src):
            raise TrashError("Refused: this location is deleted for real, not moved aside.")
        mode = item.get("mode", "dir")
        if mode not in MODES:
            raise TrashError("Unknown item mode.")
        n = len(run["entries"]) + 1
        while f"{n:03d}" in run["entries"] or (run["dir"] / f"{n:03d}").exists():
            n += 1
        eid = f"{n:03d}"
        edir = run["dir"] / eid
        entry = {"status": "moving", "mode": mode, "orig": str(src.resolve()), "name": src.name,
                 "label": str(item.get("label", ""))[:120], "group": str(item.get("group", ""))[:120],
                 "kb": int(item.get("kb", 0) or 0), "ts": time.time()}
        if mode == "contents":
            children = sorted(c.name for c in src.iterdir() if c.name != ".gitignore")
            if not children:
                raise TrashError("Nothing to move (already empty).")
            entry["children"] = children
        run["entries"][eid] = entry
        try:
            _save(run)
            edir.mkdir()
            if mode == "dir":
                _move(src, edir / src.name)
            else:
                moved = []
                try:
                    for name in entry["children"]:
                        _move(src / name, edir / name)
                        moved.append(name)
                except TrashError:
                    if not moved:
                        raise
                    entry["children"] = moved   # keep what did move; the rest stays in place
        except (TrashError, OSError) as e:
            run["entries"].pop(eid, None)
            try:
                edir.rmdir()
            except OSError:
                pass
            _save(run)
            if isinstance(e, TrashError):
                raise
            raise TrashError(f"Cannot move: {e.strerror or e}") from e
        entry["status"] = "staged"
        _save(run)
        return dict(entry, id=eid)


def _move(src, dest):
    try:
        os.rename(src, dest)
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise TrashError("On another volume: cannot move it to the DevPrune staging folder. "
                             "Use \"Delete permanently\" for this item.") from e
        raise TrashError(f"Cannot move: {e.strerror or e}") from e


# ---------------------------------------------------------------- reading runs (untrusted manifest)

def _entry_view(run, eid, e):
    """Entry as the rest of the program may see it; restorable only if every field checks out."""
    problem = ""
    ok = (isinstance(e, dict) and e.get("status") in STATUSES and e.get("mode") in MODES
          and isinstance(e.get("kb"), int) and not isinstance(e.get("kb"), bool) and e["kb"] >= 0
          and _clean_name(e.get("name")) and isinstance(e.get("orig"), str))
    if not ok:
        problem = "Unreadable record: it can be emptied but not restored."
    else:
        orig = Path(e["orig"])
        if not orig.is_absolute() or not safety.is_safe(orig) or _inside_root(orig):
            ok, problem = False, "Original location is not allowed: it can be emptied but not restored."
        elif e["mode"] == "contents" and not (isinstance(e.get("children"), list)
                                              and all(_clean_name(c) for c in e["children"])):
            ok, problem = False, "Unreadable record: it can be emptied but not restored."
    base = e if isinstance(e, dict) else {}
    return {"id": eid, "run": run["id"], "restorable": ok, "problem": problem,
            "status": base.get("status", "?") if ok else "unknown",
            "mode": base.get("mode", "dir") if ok else "dir",
            "orig": base.get("orig", "") if ok else "", "name": base.get("name", "") if ok else "",
            "label": str(base.get("label", "(unknown)"))[:120], "group": str(base.get("group", ""))[:120],
            "kb": base["kb"] if ok else 0, "children": list(base.get("children", [])) if ok and
            base.get("mode") == "contents" else []}


def list_runs():
    """All runs on disk, oldest first. Folders that do not look like runs are ignored."""
    r = root()
    out = []
    try:
        names = sorted(n for n in os.listdir(r) if RUN_RE.match(n))
    except OSError:
        return out
    for rid in names:
        d = r / rid
        if d.is_symlink() or not d.is_dir():
            continue
        orphan, raw, created = False, {}, 0.0
        try:
            data = json.loads((d / MANIFEST).read_text())
            if data.get("v") != VERSION or not isinstance(data.get("entries"), dict):
                raise ValueError("bad manifest")
            raw = data["entries"]
            created = float(data.get("created", 0))
        except (OSError, ValueError, TypeError):
            orphan = True
            try:
                created = d.stat().st_mtime
            except OSError:
                pass
        run = {"id": rid, "dir": d, "created": created, "entries": raw, "orphan": orphan}
        entries = {}
        for eid, e in raw.items():
            if ENTRY_RE.match(str(eid)):
                entries[eid] = _entry_view(run, eid, e)
        try:                                          # folders the manifest does not know about
            for n in sorted(os.listdir(d)):
                if ENTRY_RE.match(n) and n not in entries and (d / n).is_dir() and not (d / n).is_symlink():
                    entries[n] = _entry_view(run, n, None)
        except OSError:
            pass
        run["views"] = entries
        out.append(run)
    return out


def _find(run_id, entry_ids=None):
    if not isinstance(run_id, str) or not RUN_RE.match(run_id):
        raise TrashError("Unknown staging run.")
    for run in list_runs():
        if run["id"] == run_id:
            views = run["views"]
            if entry_ids is None:
                chosen = list(views)
            else:
                chosen = []
                for i in entry_ids:
                    if not isinstance(i, str) or not ENTRY_RE.match(i) or i not in views:
                        raise TrashError("Unknown staging entry.")
                    chosen.append(i)
            return run, chosen
    raise TrashError("Unknown staging run.")


def summary(now=None):
    """Cheap overview for the UI banner (no disk walking: sizes come from the manifest)."""
    now = time.time() if now is None else now
    runs = []
    for run in list_runs():
        views = run["views"].values()
        if not views:
            continue
        runs.append({"id": run["id"], "created": run["created"], "orphan": run["orphan"],
                     "count": len(views), "kb": sum(v["kb"] for v in views),
                     "age_days": max(0, (now - run["created"]) / 86400) if run["created"] else 0})
    return {"runs": runs, "count": sum(r["count"] for r in runs), "kb": sum(r["kb"] for r in runs),
            "oldest_days": max((r["age_days"] for r in runs), default=0)}


def entries(run_id):
    """The entries of one run as plain dicts (for a "Review" list)."""
    run, ids = _find(run_id)
    return [run["views"][i] for i in ids]


# ---------------------------------------------------------------- crash recovery

def reconcile():
    """Repair journals left by a crash/kill. Safe to call at every start. Returns how many entries changed."""
    changed = 0
    with _LOCK:
        for run in list_runs():
            if run["orphan"]:
                continue
            dirty = False
            for eid, e in list(run["entries"].items()):
                if not (isinstance(e, dict) and e.get("status") == "moving" and ENTRY_RE.match(str(eid))):
                    continue
                v = run["views"][eid]
                if not v["restorable"]:
                    continue
                edir = run["dir"] / eid
                if v["mode"] == "dir":
                    staged = os.path.lexists(edir / v["name"])
                    if staged:
                        e["status"] = "staged"
                    else:
                        run["entries"].pop(eid)
                else:
                    moved = [c for c in v["children"] if os.path.lexists(edir / c)]
                    if moved:
                        e["status"], e["children"] = "staged", moved
                    else:
                        run["entries"].pop(eid)
                if eid not in run["entries"]:
                    try:
                        edir.rmdir()
                    except OSError:
                        pass
                dirty, changed = True, changed + 1
            if dirty:
                _save(run)
            _drop_if_empty(run)
    return changed


def _drop_if_empty(run):
    """Remove a run folder that holds no entries and nothing else of value."""
    try:
        left = [n for n in os.listdir(run["dir"]) if n not in (MANIFEST, MANIFEST + ".tmp")]
    except OSError:
        return
    if not run["entries"] and not left and not run["orphan"]:
        shutil.rmtree(run["dir"], ignore_errors=True)


# ---------------------------------------------------------------- restore

def targets(run_id=None, entry_ids=None):
    """Validated entry views for an empty/restore job. run_id None means every run (then no ids)."""
    if run_id is None:
        if entry_ids is not None:
            raise TrashError("Entries need a run.")
        return [v for r in list_runs() for _, v in sorted(r["views"].items())]
    run, ids = _find(run_id, entry_ids)
    return [run["views"][i] for i in sorted(ids)]


def restore_conflicts(run_id=None, entry_ids=None):
    """Entries whose destination already exists: what a restore would have to replace (for the alert)."""
    if run_id is None:
        if entry_ids is not None:
            raise TrashError("Entries need a run.")
        return [c | {"run": r["id"]} for r in list_runs() for c in restore_conflicts(r["id"])]
    run, ids = _find(run_id, entry_ids)
    out = []
    for i in ids:
        v = run["views"][i]
        if not v["restorable"]:
            continue
        orig = Path(v["orig"])
        if v["mode"] == "dir":
            if os.path.lexists(orig):
                out.append({"id": i, "run": run["id"], "label": v["label"], "group": v["group"],
                            "path": str(orig)})
        else:
            hit = [c for c in v["children"] if os.path.lexists(orig / c)]
            if hit:
                out.append({"id": i, "run": run["id"], "label": v["label"], "group": v["group"],
                            "path": str(orig), "names": hit})
    return out


def restore(run_id, entry_ids=None, replace=False, stop=None):
    """Put entries back where they came from. Returns a list of {id, ok, msg}.

    If the destination exists it is only replaced when replace=True, and then it is deleted permanently
    (the caller shows the alert first). Parent folders are never created; symlinks are never followed.
    """
    results = []
    with _LOCK:
        run, ids = _find(run_id, entry_ids)
        for i in ids:
            if stop is not None and stop.is_set():
                break
            v = run["views"][i]
            if not v["restorable"]:
                results.append({"id": i, "ok": False, "msg": v["problem"]})
                continue
            if v["status"] != "staged":
                results.append({"id": i, "ok": False, "msg": "Not fully moved yet (restart DevPrune to repair)."})
                continue
            try:
                _restore_one(run, v, replace)
                results.append({"id": i, "ok": True, "msg": "Restored", "kb": v["kb"]})
            except TrashError as e:
                results.append({"id": i, "ok": False, "msg": str(e),
                                "conflict": str(e).startswith("Already exists")})
        _drop_if_empty(run)
    return results


def _remove_existing(p):
    """Delete a destination that is about to be replaced. Same checks as a delete target."""
    if p.is_symlink():
        raise TrashError("Refused: the existing path is a symlink.")
    if not safety.is_safe(p) or _inside_root(p):
        raise TrashError("Refused: the existing path is not allowed to be replaced.")
    try:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    except OSError as e:
        raise TrashError(f"Cannot replace the existing item: {e.strerror or e}") from e


def _restore_one(run, v, replace):
    eid, orig = v["id"], Path(v["orig"])
    edir = run["dir"] / eid
    if edir.is_symlink() or not edir.is_dir():
        raise TrashError("The staged copy is missing.")
    mdir = orig if v["mode"] == "contents" else orig.parent
    if not mdir.is_dir() or mdir.is_symlink():
        raise TrashError("The original folder no longer exists." if v["mode"] == "contents"
                         else "The parent folder no longer exists.")
    if v["mode"] == "dir":
        staged = edir / v["name"]
        if not os.path.lexists(staged) or staged.is_symlink():
            raise TrashError("The staged copy is missing.")
        if os.path.lexists(orig):
            if not replace:
                raise TrashError("Already exists at the original location.")
            _remove_existing(orig)
        _move(staged, orig)
        shutil.rmtree(edir, ignore_errors=True)
        run["entries"].pop(eid, None)
    else:
        left = []
        for c in v["children"]:
            s, d = edir / c, orig / c
            if not os.path.lexists(s):
                continue
            if os.path.lexists(d):
                if not replace:
                    left.append(c)
                    continue
                _remove_existing(d)
            _move(s, d)
        if left:
            run["entries"][eid]["children"] = left
            _save(run)
            raise TrashError("Already exists at the original location.")
        shutil.rmtree(edir, ignore_errors=True)
        run["entries"].pop(eid, None)
    _save(run)


# ---------------------------------------------------------------- empty

def purge(run_id, entry_ids=None, stop=None, on_item=None):
    """Delete staged entries for good. Returns a list of {id, ok, msg, kb}. Never follows symlinks."""
    results = []
    with _LOCK:
        run, ids = _find(run_id, entry_ids)
        for i in ids:
            if stop is not None and stop.is_set():
                break
            v = run["views"][i]
            if on_item:
                on_item(v)
            edir = run["dir"] / i
            if edir.is_symlink() or not _inside_root(edir):
                results.append({"id": i, "ok": False, "msg": "Refused: unsafe staging path."})
                continue
            try:
                if edir.exists():
                    shutil.rmtree(edir)
                run["entries"].pop(i, None)
                results.append({"id": i, "ok": True, "msg": "Emptied", "kb": v["kb"]})
            except OSError as e:
                results.append({"id": i, "ok": False, "msg": f"Cannot delete: {e.strerror or e}"})
        if not run["orphan"]:
            _save(run)
        left = [n for n in os.listdir(run["dir"]) if ENTRY_RE.match(n)] if run["dir"].is_dir() else []
        if not left:
            shutil.rmtree(run["dir"], ignore_errors=True)
    return results
