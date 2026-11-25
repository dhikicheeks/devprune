"""Folder size measurement (via `du`) and small subprocess helpers. All `du` runs are cancellable."""
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import state


def have(cmd):
    return shutil.which(cmd) is not None


def run_out(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def du_kb(path):
    p = None
    try:
        p = subprocess.Popen(["du", "-sk", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        state.JOB["procs"].add(p)
        out, _ = p.communicate(timeout=600)
        return int(out.split()[0]) if out.strip() else 0
    except Exception:
        if p:
            p.kill()
        return 0
    finally:
        if p:
            state.JOB["procs"].discard(p)


def du_fast(path, workers=12):
    """Measure a big folder by splitting it into many shards and running `du` on them concurrently."""
    try:
        shards, total = [str(path)], 0
        for _ in range(3):
            if len(shards) >= 64:
                break
            nxt = []
            for sh in shards:
                try:
                    with os.scandir(sh) as it:
                        for e in it:
                            if e.is_dir(follow_symlinks=False):
                                nxt.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_blocks * 512 // 1024
                except OSError:
                    nxt.append(sh)
            if not nxt or nxt == shards:
                break
            shards = nxt
        if len(shards) == 1:
            return du_kb(shards[0])
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return total + sum(ex.map(du_kb, shards))
    except Exception:
        return du_kb(path)
