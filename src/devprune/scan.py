"""Find cleanup targets (global caches + per-project artifacts) and measure them."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import cache, config, measure, paths, rules, safety, state

MIN_KB = 100  # hide targets smaller than this
PRUNE_ALWAYS = {"node_modules", "vendor", ".git", "build", ".dart_tool", "Pods", ".next",
                "Library", "Applications", ".Trash", ".gradle", ".turbo", ".idea", ".vscode"}
DEFAULT_DEPTH = 6
HEAVY_SECONDS = 3  # folders slower than this keep their cached size on a normal (non-full) scan


def _expand(p):
    return paths.HOME / p[2:] if p.startswith("~/") else Path(p)


def global_targets():
    out = []
    for r in rules.global_rules():
        req = r.get("requires")
        has_tool = measure.have(req) if req else True
        cands = r["path"] if isinstance(r["path"], list) else [r["path"]]
        if r.get("path_from_cmd") and has_tool:
            got = measure.run_out(r["path_from_cmd"])
            if got:
                cands = [got]
        path = next((p for p in map(_expand, cands) if p.exists()), None)
        if path is None:
            continue
        cmd = r.get("cmd")
        if cmd and not has_tool:
            if not r.get("fallback_delete"):
                continue  # never delete a whole folder when its official cleaner is unavailable
            cmd = None
        out.append(dict(framework="Global", group=r["group"], label=r["label"], path=str(path), note=r["note"],
                        cmd=cmd, risk=r["risk"], mode="dir", guard=r.get("guard")))
    return out


def project_targets(root, depth, cancel):
    root = Path(root).expanduser()
    found = []
    if not root.is_dir():
        return found
    base_depth = len(root.parts)
    all_rules = rules.project_rules(config.CURRENT["project_rules"])

    for dirpath, dirnames, filenames in os.walk(root):
        if cancel.is_set():
            return found
        d = Path(dirpath)
        if len(d.parts) - base_depth >= depth:
            dirnames[:] = []
        dirnames[:] = [n for n in dirnames if n not in PRUNE_ALWAYS and not n.startswith(".")
                       and not safety.inside_excluded((d / n).resolve())]
        files = set(filenames)
        pkg = ""
        if "package.json" in files:
            try:
                pkg = (d / "package.json").read_text(errors="ignore")
            except OSError:
                pass
        matched = [r for r in all_rules if rules.matches(r, files, pkg)]
        if not matched:
            continue
        fw, seen, prune = matched[0]["framework"], set(), set()
        for r in matched:
            prune |= set(r["prune_dirs"])
            for t in r["targets"]:
                if t["path"] in seen:
                    continue
                seen.add(t["path"])
                p = d / t["path"]
                if p.exists():
                    found.append(dict(framework=fw, group=d.name, label=t["label"], path=str(p), note=t["note"],
                                      cmd=None, risk=t["risk"], mode=t["mode"], guard=None, project=str(d)))
        dirnames[:] = [n for n in dirnames if n not in prune]
    return found


def do_scan(roots, depth=DEFAULT_DEPTH, full=True):
    """Return public item list, or None if cancelled."""
    job = state.JOB
    cancel = job["cancel"]
    with state.LOCK:
        prev = {i["path"]: i for i in state.STATE["items"].values()}
    job.update(phase="Looking for projects…", done=0, total=0)
    cands = global_targets()
    for r in roots:
        if cancel.is_set():
            return None
        cands += project_targets(r, depth, cancel)
    cands = [c for c in cands if safety.is_safe(c["path"])]
    job.update(phase="Measuring folder sizes…", done=0, total=len(cands))

    def measure_one(c):
        if cancel.is_set():
            c["kb"] = 0
            return c
        old = prev.get(c["path"])
        if not full and old and old.get("secs", 0) > HEAVY_SECONDS and Path(c["path"]).exists():
            c["kb"], c["secs"] = old["kb"], old["secs"]  # heavy folder: reuse the previous size
        else:
            t0 = time.time()
            c["kb"] = measure.du_fast(c["path"])
            c["secs"] = round(time.time() - t0, 1)
        job["done"] += 1
        return c

    with ThreadPoolExecutor(max_workers=3) as ex:
        cands = list(ex.map(measure_one, cands))
    if cancel.is_set():
        return None

    items, out = {}, []
    for i, c in enumerate(cands):
        if c["kb"] < MIN_KB:
            continue
        c["id"] = str(i)
        items[c["id"]] = c
        out.append(cache.pub(c))
    with state.LOCK:
        state.STATE["items"] = items
    cache.save_cache()
    out.sort(key=lambda x: -x["kb"])
    return out


def start_scan(roots, depth=DEFAULT_DEPTH, full=True):
    job = state.JOB
    with state.LOCK:
        if job["state"] == "running" or state.CJOB["state"] == "running":
            return False
        job.update(state="running", cancel=threading.Event(), procs=set(), items=[], error="",
                   phase="Starting…", done=0, total=0)

    def run():
        try:
            res = do_scan(roots, depth, full)
            job.update(state="cancelled" if res is None else "done", items=res or [])
        except Exception as e:  # keep the server alive; report to the UI
            job.update(state="error", error=str(e)[:200])

    threading.Thread(target=run, daemon=True).start()
    return True


def cancel_scan():
    state.JOB["cancel"].set()
    for p in list(state.JOB["procs"]):
        try:
            p.kill()
        except Exception:
            pass
