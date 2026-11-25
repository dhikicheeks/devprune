"""Delete selected items: official tool commands where available, otherwise remove folders/contents."""
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import cache, measure, safety, state


def wipe_contents(p):
    for child in p.iterdir():
        if child.name == ".gitignore":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def start_clean(ids, dry):
    job = state.CJOB
    with state.LOCK:
        if job["state"] == "running" or state.JOB["state"] == "running":
            return False
        plan = []
        for i in ids:
            c = state.STATE["items"].get(i)
            plan.append(dict(id=i, label=c["label"], group=c["group"], kb=c["kb"], risk=c["risk"]) if c
                        else dict(id=i, label=str(i)[:80], group="", kb=0, risk="safe"))
        job.update(state="running", total=len(ids), done=0, current="", results=[], freed_kb=0,
                   stop=threading.Event(), dry=dry, error="", plan=plan, started=time.time(), finished=0,
                   acked=False)

    def run():
        try:
            do_clean(ids, dry, job)
            job["finished"] = time.time()
            job["state"] = "stopped" if job["stop"].is_set() else "done"
        except Exception as e:
            job.update(state="error", error=str(e)[:200], finished=time.time())

    threading.Thread(target=run, daemon=True).start()
    return True


def _gradle_running():
    return subprocess.run(["pgrep", "-f", "GradleDaemon"], capture_output=True).returncode == 0


def do_clean(ids, dry, job=None):
    results, freed = [], 0
    if job is not None:
        job["results"] = results
    for n, i in enumerate(ids):
        if job is not None:
            if job["stop"].is_set():
                break
            job["done"], job["freed_kb"] = n, freed
        with state.LOCK:
            c = state.STATE["items"].get(i)
        if job is not None and c:
            job["current"] = f'{c["label"]} ({c["group"]})'
        if not c:
            results.append(dict(id=i, ok=False, msg="Not in the scan results (rescan)."))
            continue
        p = Path(c["path"])
        if not p.exists():
            results.append(dict(id=i, ok=True, msg="Already gone", kb=0))
            continue
        if p.is_symlink():
            results.append(dict(id=i, ok=False, msg="Refused: path is a symlink."))
            continue
        if not safety.is_safe(p):
            results.append(dict(id=i, ok=False, msg="Refused: unsafe path."))
            continue
        if dry:
            results.append(dict(id=i, ok=True, msg="DRY RUN: would clean", kb=c["kb"]))
            freed += c["kb"]
            continue
        if c.get("guard") == "gradle" and _gradle_running():
            results.append(dict(id=i, ok=False, msg="A Gradle daemon is still running. Run `gradle --stop` "
                                "(and `./gradlew --stop` in Android projects), then retry."))
            continue
        try:
            if c["cmd"]:
                r = subprocess.run(c["cmd"], capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout).strip()[:200] or "command failed")
            elif c["mode"] == "contents":
                wipe_contents(p)
            else:
                shutil.rmtree(p, ignore_errors=False)
            heavy_cmd = bool(c["cmd"]) and c.get("secs", 0) > 3
            after = c["kb"] if heavy_cmd else (measure.du_fast(p) if p.exists() else 0)
            gained = max(c["kb"] - after, 0)
            freed += gained
            note = " (freed size shows after a full scan)" if heavy_cmd else ""
            results.append(dict(id=i, ok=True, msg="Done" + note, kb=gained))
        except Exception as e:
            results.append(dict(id=i, ok=False, msg=str(e)[:200]))
    if not dry:
        with state.LOCK:
            for r in results:
                if r["ok"]:
                    state.STATE["items"].pop(r["id"], None)
        cache.save_cache()
    if job is not None:
        job["done"], job["freed_kb"] = len(results), freed
    return dict(results=results, freed_kb=freed)
