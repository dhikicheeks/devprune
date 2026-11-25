import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from devprune import config, paths, safety, state  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake HOME inside tmp_path so tests never touch the real home."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(paths, "HOME", h)
    state.STATE["items"] = {}
    state.CACHE_TS[0] = None
    state.JOB.update(state="idle", cancel=threading.Event(), procs=set(), items=[], done=0, total=0)
    state.CJOB.update(state="idle", stop=threading.Event(), results=[], done=0, total=0, plan=[], acked=True)
    safety.EXCLUDE[:] = []
    config.CURRENT.update(roots=[], exclude=[], project_rules=[])
    yield h
    state.STATE["items"] = {}
    safety.EXCLUDE[:] = []


def make_item(item_id, path, **kw):
    base = dict(id=str(item_id), framework="Node.js", group="g", label="x", path=str(path), note="",
                risk="safe", kb=100, cmd=None, mode="dir", guard=None, secs=0)
    base.update(kw)
    return base


def register(*items):
    state.STATE["items"] = {i["id"]: i for i in items}


def wait_clean():
    import time
    for _ in range(300):
        if state.CJOB["state"] != "running":
            return
        time.sleep(0.02)
    raise AssertionError("clean job did not finish")
