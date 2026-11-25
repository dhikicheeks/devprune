import signal
import threading
import time

import pytest
from conftest import make_item, register

from devprune import cli, lifecycle, server, state, trash


class FakeSrv:
    def __init__(self):
        self.down = threading.Event()

    def shutdown(self):
        self.down.set()

    def serve_forever(self):
        raise KeyboardInterrupt


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def running(**kw):
    base = dict(state="running", kind="clean", dry=False, permanent=False, total=5, done=1,
                current="pnpm cache (pnpm / Node)", stop=threading.Event())
    base.update(kw)
    state.CJOB.update(base)


@pytest.fixture
def guard(home):
    said = []
    clock = Clock()
    g = lifecycle.Guard(FakeSrv(), say=said.append, clock=clock)
    g.said, g.clock_ = said, clock
    yield g
    state.CJOB.update(state="idle", dry=False, kind="clean", permanent=True)


# ---- Ctrl+C

def test_ctrl_c_without_a_job_quits_at_once(guard):
    with pytest.raises(KeyboardInterrupt):
        guard.on_int(signal.SIGINT, None)
    assert guard.said == [] and guard.exit_code == 0


def test_ctrl_c_during_a_simulation_quits_at_once(guard):
    running(dry=True)
    with pytest.raises(KeyboardInterrupt):
        guard.on_int(signal.SIGINT, None)


def test_first_ctrl_c_only_warns(guard):
    running()
    guard.on_int(signal.SIGINT, None)                 # must not raise
    text = guard.said[0]
    assert "cleanup is running: item 2 of 5" in text and "pnpm cache" in text
    assert "can be restored" in text and "Ctrl+C again within 5 s" in text
    assert not state.CJOB["stop"].is_set() and not guard.srv.down.is_set()


def test_second_ctrl_c_stops_after_the_current_item_then_shuts_down(guard):
    running()
    guard.on_int(signal.SIGINT, None)
    guard.clock_.t += 2
    guard.on_int(signal.SIGINT, None)
    assert state.CJOB["stop"].is_set() and "Stopping after the current item" in guard.said[-1]
    assert not guard.srv.down.wait(0.2)               # still waiting for the running item
    state.CJOB["state"] = "stopped"                   # the job thread finished its item
    assert guard.srv.down.wait(2)


def test_third_ctrl_c_quits_immediately(guard):
    running()
    guard.on_int(signal.SIGINT, None)
    guard.on_int(signal.SIGINT, None)
    with pytest.raises(KeyboardInterrupt):
        guard.on_int(signal.SIGINT, None)
    assert guard.said[-1] == "Quitting now."
    state.CJOB["state"] = "done"


def test_second_ctrl_c_after_the_window_is_a_new_first(guard):
    running()
    guard.on_int(signal.SIGINT, None)
    guard.clock_.t += lifecycle.WINDOW + 1
    guard.on_int(signal.SIGINT, None)                 # too late: warns again, does not stop
    assert len(guard.said) == 2 and not state.CJOB["stop"].is_set()


@pytest.mark.parametrize("kind,permanent,expect", [
    ("clean", False, "moved aside stay in DevPrune"),
    ("clean", True, "already deleted cannot be restored"),
    ("empty", True, "emptying is running"),
    ("restore", True, "restore is running"),
])
def test_warning_text_matches_the_job(guard, kind, permanent, expect):
    running(kind=kind, permanent=permanent)
    guard.on_int(signal.SIGINT, None)
    assert expect.lower() in guard.said[0].lower()


def test_a_closed_terminal_does_not_crash_the_handler(home):
    def broken(*a, **k):
        raise OSError("terminal gone")
    g = lifecycle.Guard(FakeSrv(), say=broken)
    running()
    g.on_int(signal.SIGINT, None)                     # printing fails silently
    state.CJOB["state"] = "idle"


# ---- SIGTERM / SIGHUP

@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_term_and_hup_without_a_job_quit_with_the_conventional_code(guard, sig):
    with pytest.raises(KeyboardInterrupt):
        guard.on_term(sig, None)
    assert guard.exit_code == 128 + sig


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_term_and_hup_with_a_job_stop_after_the_item(guard, sig):
    running()
    guard.on_term(sig, None)                          # no question asked, no raise
    assert state.CJOB["stop"].is_set() and guard.exit_code == 128 + sig
    assert not guard.srv.down.wait(0.2)
    state.CJOB["state"] = "stopped"
    assert guard.srv.down.wait(2)


def test_shutdown_is_forced_after_the_grace_period(guard, monkeypatch):
    monkeypatch.setattr(lifecycle, "GRACE_TERM", 0.3)
    running()                                         # the current item never finishes
    guard.on_term(signal.SIGTERM, None)
    assert guard.srv.down.wait(2)
    state.CJOB["state"] = "idle"


# ---- install / restore and cli

def test_install_and_restore_put_the_old_handlers_back(guard):
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    guard.install()
    assert signal.getsignal(signal.SIGINT) == guard.on_int
    assert signal.getsignal(signal.SIGTERM) == guard.on_term
    guard.restore()
    assert {s: signal.getsignal(s) for s in before} == before


def test_cli_leaves_the_handlers_as_it_found_them(home, monkeypatch):
    monkeypatch.setattr(server, "make_server", lambda port: FakeSrv())
    before = signal.getsignal(signal.SIGINT)
    assert cli.main(["--no-browser"]) == 0
    assert signal.getsignal(signal.SIGINT) == before


def test_cli_reminds_about_held_items_on_exit(home, monkeypatch, capsys):
    monkeypatch.setattr(server, "make_server", lambda port: FakeSrv())
    cli.main(["--no-browser"])
    assert "holding" not in capsys.readouterr().out
    from conftest import wait_clean
    from test_clean import populated
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d, kb=2048))
    from devprune import clean
    clean.start_clean(["1"], False, permanent=False)
    wait_clean()
    cli.main(["--no-browser"])
    out = capsys.readouterr().out
    assert "DevPrune is holding 1 item (2.0 MB)" in out and "restore or empty" in out
    assert trash.summary()["count"] == 1


def test_cli_returns_the_exit_code_chosen_by_the_signal_handler(home, monkeypatch):
    class Srv(FakeSrv):
        def serve_forever(self):
            signal.raise_signal(signal.SIGTERM)       # no job running: the handler quits the loop
    monkeypatch.setattr(server, "make_server", lambda port: Srv())
    assert cli.main(["--no-browser"]) == 143


def test_job_stop_flag_is_what_the_job_loop_reads(home):
    from conftest import wait_clean
    from test_clean import populated
    dirs = [populated(home / "Developer" / f"p{k}" / "node_modules") for k in range(4)]
    register(*[make_item(k, d) for k, d in enumerate(dirs)])
    from devprune import clean
    real = time.sleep
    started = threading.Event()
    import shutil
    orig = shutil.rmtree

    def slow(p, *a, **k):
        started.set()
        real(0.2)
        return orig(p, *a, **k)
    shutil.rmtree = slow
    try:
        assert clean.start_clean([str(k) for k in range(4)], False, permanent=True)
        assert started.wait(2)
        g = lifecycle.Guard(FakeSrv(), say=lambda *a, **k: None)
        g.on_int(signal.SIGINT, None)
        g.on_int(signal.SIGINT, None)
        wait_clean()
    finally:
        shutil.rmtree = orig
    assert state.CJOB["state"] == "stopped" and sum(d.exists() for d in dirs) >= 2
