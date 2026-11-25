"""Real signals sent to a real DevPrune process (HOME is a temp folder, rmtree is slowed down)."""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")
ITEM_SECONDS = 0.8
CHILD = f"""
import shutil, sys, time
sys.path.insert(0, {SRC!r})
real = shutil.rmtree
def slow(p, *a, **k):
    time.sleep({ITEM_SECONDS})
    return real(p, *a, **k)
shutil.rmtree = slow
from devprune import cli
sys.exit(cli.main(["--no-browser", "--port", sys.argv[1]]))
"""


class Child:
    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.dirs = []
        for k in range(3):
            app = self.home / "Developer" / f"app{k}"
            (app / "node_modules").mkdir(parents=True)
            (app / "package.json").write_text("{}")
            (app / "node_modules" / "a").write_bytes(b"x" * 300_000)
            self.dirs.append(app / "node_modules")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        script = tmp_path / "child.py"
        script.write_text(CHILD)
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        self.proc = subprocess.Popen([sys.executable, str(script), str(self.port)], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        page = self._wait_for_page()
        self.token = re.search(r'data-token="([^"]+)"', page).group(1)

    def _wait_for_page(self):
        for _ in range(100):
            try:
                return urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=2).read().decode()
            except OSError:
                time.sleep(0.1)
        raise AssertionError("DevPrune did not start")

    def post(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body or {}).encode(),
                                     {"X-Token": self.token, "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=5))

    def start_real_clean(self):
        self.post("/api/scan", {"roots": [str(self.home / "Developer")], "full": True})
        for _ in range(100):
            st = self.post("/api/status")
            if st["state"] != "running":
                break
            time.sleep(0.1)
        ids = [i["id"] for i in st["items"]]
        assert len(ids) == 3
        assert self.post("/api/clean", {"ids": ids, "dry": False, "permanent": True})["started"]
        assert self.post("/api/clean_status")["state"] == "running"

    def left(self):
        return sum(d.exists() for d in self.dirs)

    def send(self, sig, n=1, gap=0.1):
        for _ in range(n):
            self.proc.send_signal(sig)
            time.sleep(gap)

    def finish(self, timeout):
        try:
            out, _ = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            self.proc.kill()
            out, _ = self.proc.communicate()
            raise AssertionError("DevPrune did not exit in time:\n" + out) from e
        return self.proc.returncode, out

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.communicate()


@pytest.fixture
def child(tmp_path):
    c = Child(tmp_path)
    yield c
    c.close()


def test_ctrl_c_with_nothing_running_quits_at_once(child):
    child.send(signal.SIGINT, gap=0)
    code, out = child.finish(5)
    assert code == 0 and "Traceback" not in out


def test_first_ctrl_c_then_the_job_completes_untouched(child):
    child.start_real_clean()
    child.send(signal.SIGINT, gap=0.2)
    for _ in range(100):
        if child.post("/api/clean_status")["state"] != "running":
            break
        time.sleep(0.1)
    assert child.post("/api/clean_status")["state"] == "done" and child.left() == 0
    child.send(signal.SIGINT, gap=0)
    code, out = child.finish(5)
    assert code == 0 and "A cleanup is running" in out and "Press Ctrl+C again within 5 s" in out


def test_two_ctrl_c_stop_after_the_current_item(child):
    child.start_real_clean()
    child.send(signal.SIGINT, n=2, gap=0.2)
    code, out = child.finish(6)
    assert code == 0 and "Stopping after the current item" in out and "Traceback" not in out
    assert 1 <= child.left() <= 2                          # the item in flight finished, the rest were kept


def test_three_ctrl_c_quit_immediately(child):
    child.start_real_clean()
    t = time.time()
    child.send(signal.SIGINT, n=3, gap=0.1)
    code, out = child.finish(3)
    assert time.time() - t < 2.5 and "Quitting now." in out
    assert child.left() >= 1


@pytest.mark.parametrize("sig,code", [(signal.SIGTERM, 143), (signal.SIGHUP, 129)])
def test_term_and_hup_finish_the_item_and_exit_with_the_signal_code(child, sig, code):
    child.start_real_clean()
    child.send(sig, gap=0.1)
    rc, out = child.finish(8)
    assert rc == code and "stopping after the current item" in out.lower() and "Traceback" not in out
    assert 1 <= child.left() <= 2


@pytest.mark.parametrize("sig,code", [(signal.SIGTERM, 143), (signal.SIGHUP, 129)])
def test_term_and_hup_with_nothing_running_exit_at_once(child, sig, code):
    child.send(sig, gap=0)
    rc, out = child.finish(5)
    assert rc == code


def test_staged_items_survive_an_interrupted_run_and_are_listed_next_time(child):
    child.post("/api/scan", {"roots": [str(child.home / "Developer")], "full": True})
    for _ in range(100):
        st = child.post("/api/status")
        if st["state"] != "running":
            break
        time.sleep(0.1)
    ids = [i["id"] for i in st["items"]]
    assert child.post("/api/clean", {"ids": ids, "dry": False})["started"]       # default: staging
    for _ in range(100):
        if child.post("/api/clean_status")["state"] != "running":
            break
        time.sleep(0.05)
    assert child.left() == 0 and child.post("/api/trash")["count"] == 3
    child.send(signal.SIGINT, gap=0)
    code, out = child.finish(5)
    assert code == 0 and "DevPrune is holding 3 items" in out
    assert os.path.isdir(child.home / ".cache" / "devprune" / "trash")
