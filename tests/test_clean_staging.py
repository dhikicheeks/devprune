import errno
import os
import subprocess

from conftest import make_item, register, wait_clean
from test_clean import populated

from devprune import cache, clean, paths, server, state, trash


def run_clean(ids, dry=False, permanent=False):
    out = clean.do_clean(ids, dry, None, permanent)
    return {r["id"]: r for r in out["results"]}, out


def test_library_default_stays_permanent_for_direct_callers(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d))
    assert clean.do_clean(["1"], False)["results"][0]["ok"]
    assert not d.exists() and trash.summary()["count"] == 0


def test_folder_is_moved_to_staging_and_not_counted_as_freed(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d, kb=500))
    r, out = run_clean(["1"])
    assert r["1"]["ok"] and r["1"]["staged"] and r["1"]["kb"] == 0 and r["1"]["held_kb"] == 500
    assert out["freed_kb"] == 0 and out["held_kb"] == 500 and out["run"]
    assert not d.exists() and "1" not in state.STATE["items"]
    s = trash.summary()
    assert s["count"] == 1 and s["kb"] == 500 and s["runs"][0]["id"] == out["run"]


def test_moved_item_can_be_restored(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d))
    _, out = run_clean(["1"])
    assert trash.restore(out["run"])[0]["ok"] and (d / "sub" / "g").exists()


def test_dry_run_with_staging_moves_nothing_and_creates_no_run(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d, kb=500))
    r, out = run_clean(["1"], dry=True)
    assert r["1"]["ok"] and "move to DevPrune" in r["1"]["msg"] and r["1"]["held_kb"] == 500
    assert d.exists() and out["run"] == "" and not paths.trash_root().exists()


def test_command_items_stay_permanent(home, monkeypatch):
    d = populated(home / "Library" / "pnpm" / "store")
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    register(make_item(1, d, cmd=["pnpm", "store", "prune"], kb=900))
    r, out = run_clean(["1"])
    assert calls == [["pnpm", "store", "prune"]] and not r["1"].get("staged")
    assert out["run"] == "" and trash.summary()["count"] == 0


def test_macos_trash_is_emptied_not_staged(home):
    t = populated(home / ".Trash")
    register(make_item(1, t, group="System", label="Trash"))
    r, out = run_clean(["1"])
    assert r["1"]["ok"] and not r["1"].get("staged") and not t.exists()
    assert out["run"] == "" and trash.summary()["count"] == 0
    assert trash.can_stage(home / ".Trash") is False and trash.can_stage(home / ".Trash" / "x") is False


def test_contents_mode_staged_keeps_folder_and_gitignore(home):
    d = populated(home / "Developer" / "shop" / "storage" / "logs")
    (d / ".gitignore").write_text("*\n")
    register(make_item(1, d, mode="contents"))
    r, out = run_clean(["1"])
    assert r["1"]["staged"] and [c.name for c in d.iterdir()] == [".gitignore"]
    assert trash.restore(out["run"])[0]["ok"] and (d / "sub" / "g").exists()


def test_empty_contents_folder_is_not_an_error(home):
    d = home / "Developer" / "shop" / "storage" / "logs"
    d.mkdir(parents=True)
    (d / ".gitignore").write_text("*\n")
    register(make_item(1, d, mode="contents"))
    r, out = run_clean(["1"])
    assert r["1"]["ok"] and r["1"]["msg"] == "Nothing to clean" and out["run"] == ""


def test_staging_failure_keeps_item_and_leaves_no_run(home, monkeypatch):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d))

    def boom(a, b):
        raise OSError(errno.EXDEV, "cross-device")
    monkeypatch.setattr(os, "rename", boom)
    r, out = run_clean(["1"])
    assert not r["1"]["ok"] and "another volume" in r["1"]["msg"]
    assert (d / "sub" / "g").exists() and "1" in state.STATE["items"]
    assert out["run"] == "" and trash.summary()["count"] == 0


def test_gradle_guard_still_applies_before_staging(home, monkeypatch):
    d = populated(home / ".gradle" / "caches")
    monkeypatch.setattr(clean, "_gradle_running", lambda: True)
    register(make_item(1, d, guard="gradle"))
    r, out = run_clean(["1"])
    assert not r["1"]["ok"] and d.exists() and out["run"] == ""


def test_unsafe_and_symlink_refused_before_staging(home):
    docs = populated(home / "Documents")
    real = populated(home / "Developer" / "real")
    link = home / "Developer" / "link"
    link.symlink_to(real)
    register(make_item(1, docs), make_item(2, link))
    r, out = run_clean(["1", "2"])
    assert not r["1"]["ok"] and not r["2"]["ok"] and docs.exists() and real.exists() and out["run"] == ""


def test_mixed_selection_uses_one_run(home, monkeypatch):
    a = populated(home / "Developer" / "a" / "node_modules")
    b = populated(home / "Developer" / "b" / ".next")
    c = populated(home / "Library" / "Caches" / "Homebrew")
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a_, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    register(make_item(1, a, kb=10), make_item(2, b, kb=20), make_item(3, c, cmd=["brew", "cleanup", "-s"], kb=30))
    r, out = run_clean(["1", "2", "3"])
    assert r["1"]["staged"] and r["2"]["staged"] and not r["3"].get("staged")
    assert out["held_kb"] == 30 and len(trash.summary()["runs"]) == 1 and trash.summary()["count"] == 2


def test_job_reports_held_and_run(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d, kb=500))
    assert clean.start_clean(["1"], False, permanent=False)
    wait_clean()
    st = server._clean_status({})
    assert st["state"] == "done" and st["held_kb"] == 500 and st["freed_kb"] == 0
    assert st["run"] and st["permanent"] is False and st["results"][0]["staged"]


def test_route_defaults_to_staging_and_only_a_real_true_means_permanent(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    register(make_item(1, d))
    assert server._clean({"ids": ["1"], "dry": False})["started"]                  # nothing said: staging
    wait_clean()
    assert state.CJOB["permanent"] is False and not d.exists() and trash.summary()["count"] == 1
    d2 = populated(home / "Developer" / "b" / "node_modules")
    register(make_item(2, d2))
    assert server._clean({"ids": ["2"], "dry": False, "permanent": "yes"})["started"]   # not a real true
    wait_clean()
    assert state.CJOB["permanent"] is False and trash.summary()["count"] == 2
    d3 = populated(home / "Developer" / "c" / "node_modules")
    register(make_item(3, d3))
    assert server._clean({"ids": ["3"], "dry": False, "permanent": True})["started"]
    wait_clean()
    assert state.CJOB["permanent"] is True and not d3.exists() and trash.summary()["count"] == 2


def test_pub_tells_the_ui_what_can_be_staged(home):
    plain = populated(home / "Developer" / "a" / "node_modules")
    t = populated(home / ".Trash")
    assert cache.pub(make_item(1, plain))["can_stage"] is True
    assert cache.pub(make_item(2, plain, cmd=["npm", "cache", "clean", "--force"]))["can_stage"] is False
    assert cache.pub(make_item(3, t))["can_stage"] is False
