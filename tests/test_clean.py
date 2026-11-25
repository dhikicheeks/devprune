import os
import shutil
import subprocess

from conftest import make_item, register, wait_clean

from devprune import clean, state


def populated(path, n=3):
    path.mkdir(parents=True)
    for k in range(n):
        (path / f"f{k}").write_text("data")
    (path / "sub").mkdir()
    (path / "sub" / "g").write_text("data")
    return path


def run_clean(ids, dry=False):
    return {r["id"]: r for r in clean.do_clean(ids, dry)["results"]}


def test_dry_run_deletes_nothing(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d, kb=500))
    r = run_clean(["1"], dry=True)
    assert r["1"]["ok"] and d.exists()


def test_deletes_folder(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d))
    assert run_clean(["1"])["1"]["ok"]
    assert not d.exists()
    assert "1" not in state.STATE["items"]  # removed from state after success


def test_contents_mode_keeps_folder_and_gitignore(home):
    d = populated(home / "Developer" / "shop" / "storage" / "logs")
    (d / ".gitignore").write_text("*\n")
    register(make_item(1, d, mode="contents"))
    assert run_clean(["1"])["1"]["ok"]
    assert d.is_dir() and [p.name for p in d.iterdir()] == [".gitignore"]


def test_path_already_gone(home):
    register(make_item(1, home / "Developer" / "hilang" / "node_modules"))
    r = run_clean(["1"])["1"]
    assert r["ok"] and r["kb"] == 0


def test_unknown_id_refused(home):
    register()
    assert run_clean(["999"])["999"]["ok"] is False


def test_unsafe_paths_refused_and_untouched(home):
    docs = populated(home / "Documents")
    ssh = populated(home / ".ssh")
    register(make_item(1, docs), make_item(2, ssh))
    r = run_clean(["1", "2"])
    assert not r["1"]["ok"] and not r["2"]["ok"]
    assert (docs / "f0").exists() and (ssh / "f0").exists()


def test_symlink_refused_target_intact(home, tmp_path):
    target = populated(tmp_path / "target")
    link = home / "Developer" / "app" / "node_modules"
    link.parent.mkdir(parents=True)
    os.symlink(target, link)
    register(make_item(1, link), make_item(2, link, mode="contents"))
    r = run_clean(["1", "2"])
    assert not r["1"]["ok"] and not r["2"]["ok"]
    assert (target / "f0").exists()


def test_official_command_runs_instead_of_rmtree(home, monkeypatch):
    d = populated(home / ".pub-cache")
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    register(make_item(1, d, cmd=["dart", "pub", "cache", "clean", "--force"]))
    assert run_clean(["1"])["1"]["ok"]
    assert calls[0] == ["dart", "pub", "cache", "clean", "--force"]
    assert (d / "f0").exists()  # deletion is left to the tool


def test_failed_command_is_reported(home, monkeypatch):
    d = populated(home / ".pub-cache")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    register(make_item(1, d, cmd=["dart", "pub", "cache", "clean", "--force"]))
    r = run_clean(["1"])["1"]
    assert not r["ok"] and "boom" in r["msg"]


def test_gradle_refused_while_daemon_runs(home, monkeypatch):
    d = populated(home / ".gradle" / "caches")
    register(make_item(1, d, guard="gradle"))
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert not run_clean(["1"])["1"]["ok"] and d.exists()
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, "", ""))
    register(make_item(1, d, guard="gradle"))
    assert run_clean(["1"])["1"]["ok"] and not d.exists()


def test_one_failure_does_not_stop_the_rest(home):
    bad = populated(home / "Documents")
    good = populated(home / "Developer" / "a" / "node_modules")
    register(make_item(1, bad), make_item(2, good))
    r = run_clean(["1", "2"])
    assert not r["1"]["ok"] and r["2"]["ok"] and not good.exists()


def test_background_job_and_stop(home, monkeypatch):
    import time
    dirs = [populated(home / "Developer" / f"p{k}" / "node_modules") for k in range(6)]
    register(*[make_item(k, d) for k, d in enumerate(dirs)])
    real = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda p, *a, **k: (time.sleep(0.15), real(p, *a, **k))[1])
    assert clean.start_clean([str(k) for k in range(6)], False)
    assert clean.start_clean(["0"], False) is False  # no concurrent jobs
    while state.CJOB["done"] < 2:
        time.sleep(0.02)
    state.CJOB["stop"].set()
    wait_clean()
    assert state.CJOB["state"] == "stopped"
    left = sum(d.exists() for d in dirs)
    assert 0 < left < 6
