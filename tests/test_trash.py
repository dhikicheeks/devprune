import errno
import json
import os
import shutil
import stat
import threading
import time

import pytest
from conftest import make_item

from devprune import paths, safety, trash


def populated(path, n=3):
    path.mkdir(parents=True)
    for k in range(n):
        (path / f"f{k}").write_text("data")
    (path / "sub").mkdir()
    (path / "sub" / "g").write_text("data")
    return path


def stage_one(home, name="node_modules", **kw):
    d = populated(home / "Developer" / "app" / name)
    run = trash.create_run()
    entry = trash.stage(run, make_item(1, d, kb=500, label=name, group="app", **kw), d)
    return run, entry, d


def manifest(run):
    return json.loads((run["dir"] / trash.MANIFEST).read_text())


# ---- staging

def test_stage_moves_folder_and_records_entry(home):
    run, entry, d = stage_one(home)
    assert not d.exists()
    assert (run["dir"] / entry["id"] / "node_modules" / "sub" / "g").read_text() == "data"
    e = manifest(run)["entries"][entry["id"]]
    assert e["status"] == "staged" and e["kb"] == 500 and e["orig"] == str(d.resolve())


def test_root_is_private_and_inside_home(home):
    trash.create_run()
    r = paths.trash_root()
    assert r.is_relative_to(home) and stat.S_IMODE(r.stat().st_mode) == 0o700


def test_staging_is_fast_for_many_files(home):
    d = home / "Developer" / "big" / "node_modules"
    d.mkdir(parents=True)
    for k in range(2000):
        (d / f"f{k}").write_text("x")
    run = trash.create_run()
    t = time.time()
    trash.stage(run, make_item(1, d), d)
    assert time.time() - t < 1


def test_refuses_symlink_unsafe_and_staging_paths(home):
    run = trash.create_run()
    real = populated(home / "Developer" / "real")
    link = home / "Developer" / "link"
    link.symlink_to(real)
    with pytest.raises(trash.TrashError, match="symlink"):
        trash.stage(run, make_item(1, link), link)
    docs = home / "Documents"
    docs.mkdir()
    with pytest.raises(trash.TrashError, match="unsafe"):
        trash.stage(run, make_item(2, docs), docs)
    with pytest.raises(trash.TrashError):
        trash.stage(run, make_item(3, run["dir"]), run["dir"])
    assert real.exists() and docs.exists() and manifest(run)["entries"] == {}


def test_contents_mode_keeps_folder_and_gitignore(home):
    d = populated(home / "Developer" / "shop" / "storage" / "logs")
    (d / ".gitignore").write_text("*\n")
    run = trash.create_run()
    entry = trash.stage(run, make_item(1, d, mode="contents"), d)
    assert d.is_dir() and [c.name for c in d.iterdir()] == [".gitignore"]
    assert sorted(entry["children"]) == ["f0", "f1", "f2", "sub"]
    assert (run["dir"] / entry["id"] / "sub" / "g").exists()


def test_contents_mode_empty_folder_is_not_an_entry(home):
    d = home / "Developer" / "shop" / "storage" / "logs"
    d.mkdir(parents=True)
    (d / ".gitignore").write_text("*\n")
    run = trash.create_run()
    with pytest.raises(trash.TrashError, match="Nothing to move"):
        trash.stage(run, make_item(1, d, mode="contents"), d)
    assert manifest(run)["entries"] == {}


def test_cross_volume_rename_fails_cleanly(home, monkeypatch):
    d = populated(home / "Developer" / "app" / "node_modules")
    run = trash.create_run()

    def boom(a, b):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(trash.TrashError, match="another volume"):
        trash.stage(run, make_item(1, d), d)
    assert (d / "sub" / "g").exists() and manifest(run)["entries"] == {}
    assert [n for n in os.listdir(run["dir"]) if n != trash.MANIFEST] == []


def test_manifest_write_is_atomic_and_leaves_no_temp(home):
    run, _, _ = stage_one(home)
    assert sorted(os.listdir(run["dir"])) == ["001", trash.MANIFEST]
    json.loads((run["dir"] / trash.MANIFEST).read_text())


# ---- listing, summary

def test_summary_counts_from_manifest(home):
    stage_one(home)
    s = trash.summary()
    assert s["count"] == 1 and s["kb"] == 500 and len(s["runs"]) == 1
    assert trash.summary(now=time.time() + 8 * 86400)["oldest_days"] >= 7.9


def test_summary_empty(home):
    assert trash.summary() == {"runs": [], "count": 0, "kb": 0, "oldest_days": 0}


# ---- restore

def test_restore_puts_folder_back_and_removes_run(home):
    run, entry, d = stage_one(home)
    res = trash.restore(run["id"], [entry["id"]])
    assert res[0]["ok"] and (d / "sub" / "g").read_text() == "data"
    assert not run["dir"].exists()


def test_restore_conflict_is_reported_without_replace(home):
    run, entry, d = stage_one(home)
    d.mkdir()
    (d / "new").write_text("regenerated")
    assert [c["id"] for c in trash.restore_conflicts(run["id"])] == [entry["id"]]
    res = trash.restore(run["id"], [entry["id"]])
    assert not res[0]["ok"] and res[0]["conflict"]
    assert (d / "new").exists() and (run["dir"] / entry["id"] / "node_modules").exists()


def test_restore_replace_deletes_existing_then_restores(home):
    run, entry, d = stage_one(home)
    d.mkdir()
    (d / "new").write_text("regenerated")
    res = trash.restore(run["id"], [entry["id"]], replace=True)
    assert res[0]["ok"] and not (d / "new").exists() and (d / "sub" / "g").exists()


def test_restore_replace_refuses_symlink_destination(home):
    run, entry, d = stage_one(home)
    other = home / "Developer" / "other"
    other.mkdir()
    (other / "keep").write_text("k")
    d.symlink_to(other)
    res = trash.restore(run["id"], [entry["id"]], replace=True)
    assert not res[0]["ok"] and "symlink" in res[0]["msg"]
    assert (other / "keep").exists() and d.is_symlink()


def test_restore_never_creates_missing_parent(home):
    run, entry, d = stage_one(home)
    shutil.rmtree(d.parent)
    res = trash.restore(run["id"], [entry["id"]])
    assert not res[0]["ok"] and "parent" in res[0]["msg"] and not d.parent.exists()


def test_restore_contents_mode(home):
    d = populated(home / "Developer" / "shop" / "storage" / "logs")
    (d / ".gitignore").write_text("*\n")
    run = trash.create_run()
    entry = trash.stage(run, make_item(1, d, mode="contents"), d)
    (d / "f0").write_text("newer")                      # one child regenerated meanwhile
    res = trash.restore(run["id"], [entry["id"]])
    assert not res[0]["ok"] and res[0]["conflict"]
    assert (d / "f0").read_text() == "newer" and (d / "sub" / "g").exists()   # the others came back
    res = trash.restore(run["id"], [entry["id"]], replace=True)
    assert res[0]["ok"] and (d / "f0").read_text() == "data" and not run["dir"].exists()


def test_restore_honours_new_exclude(home):
    run, entry, d = stage_one(home)
    safety.EXCLUDE[:] = [d.resolve()]
    res = trash.restore(run["id"], [entry["id"]])
    assert not res[0]["ok"] and not d.exists()


# ---- empty

def test_purge_removes_data_and_run(home):
    run, entry, _ = stage_one(home)
    res = trash.purge(run["id"], [entry["id"]])
    assert res[0]["ok"] and res[0]["kb"] == 500 and not run["dir"].exists()
    assert trash.summary()["count"] == 0


def test_purge_all_entries_of_run_by_default(home):
    run, _, _ = stage_one(home)
    d2 = populated(home / "Developer" / "app" / "build")
    trash.stage(run, make_item(2, d2), d2)
    assert len(trash.purge(run["id"])) == 2 and not run["dir"].exists()


def test_purge_does_not_follow_symlinks_inside_staged_tree(home):
    outside = home / "Developer" / "outside"
    outside.mkdir(parents=True)
    (outside / "precious").write_text("p")
    d = populated(home / "Developer" / "app" / "node_modules")
    (d / "evil").symlink_to(outside)
    run = trash.create_run()
    entry = trash.stage(run, make_item(1, d), d)
    assert trash.purge(run["id"], [entry["id"]])[0]["ok"]
    assert (outside / "precious").read_text() == "p"


def test_purge_stop_event_leaves_remaining(home):
    run, _, _ = stage_one(home)
    d2 = populated(home / "Developer" / "app" / "build")
    trash.stage(run, make_item(2, d2), d2)
    ev = threading.Event()
    ev.set()
    assert trash.purge(run["id"], stop=ev) == [] and run["dir"].exists()


# ---- crash recovery and hostile manifests

def test_reconcile_moving_entry_after_rename_became_staged(home):
    run, entry, d = stage_one(home)
    m = manifest(run)
    m["entries"][entry["id"]]["status"] = "moving"      # crash between rename and journal update
    (run["dir"] / trash.MANIFEST).write_text(json.dumps(m))
    assert trash.reconcile() == 1
    assert manifest(run)["entries"][entry["id"]]["status"] == "staged"
    assert trash.restore(run["id"], [entry["id"]])[0]["ok"] and d.exists()


def test_reconcile_moving_entry_that_never_moved_is_dropped(home):
    d = populated(home / "Developer" / "app" / "node_modules")
    run = trash.create_run()
    m = {"v": 1, "created": time.time(), "entries": {"001": {
        "status": "moving", "mode": "dir", "orig": str(d.resolve()), "name": "node_modules",
        "label": "x", "group": "g", "kb": 5, "ts": 0}}}
    (run["dir"] / trash.MANIFEST).write_text(json.dumps(m))
    (run["dir"] / "001").mkdir()                        # crash after mkdir, before rename
    assert trash.reconcile() == 1
    assert d.exists() and not run["dir"].exists()


def test_corrupt_manifest_becomes_orphan_that_can_only_be_emptied(home):
    run, entry, d = stage_one(home)
    (run["dir"] / trash.MANIFEST).write_text("{not json")
    s = trash.summary()
    assert s["runs"][0]["orphan"] and s["count"] == 1
    res = trash.restore(run["id"])
    assert not res[0]["ok"] and not d.exists()
    assert trash.purge(run["id"])[0]["ok"] and not run["dir"].exists()


def test_hostile_manifest_entries_are_not_restorable(home):
    victim = home / ".ssh" / "keys"                     # sensitive folder: never a valid target
    victim.mkdir(parents=True)
    (victim / "f").write_text("v")
    run, entry, _ = stage_one(home)
    m = manifest(run)
    e = m["entries"][entry["id"]]
    m["entries"].update({
        "002": dict(e, orig=str(victim)),               # points at a sensitive folder
        "003": dict(e, name="../../Documents"),         # path traversal in name
        "004": dict(e, kb="999"),                       # wrong type
        "005": dict(e, status="deleted"),               # unknown status
        "../x": dict(e),                                # bad id
    })
    (run["dir"] / trash.MANIFEST).write_text(json.dumps(m))
    views = {v["id"]: v for v in trash.entries(run["id"])}
    assert views[entry["id"]]["restorable"]
    assert not any(views[i]["restorable"] for i in ("002", "003", "004", "005"))
    assert "../x" not in views
    for i in ("002", "003", "004", "005"):
        assert not trash.restore(run["id"], [i])[0]["ok"]
    assert (victim / "f").exists()


def test_ids_from_clients_are_validated(home):
    run, entry, _ = stage_one(home)
    for bad in ("../..", "001/../../x", "1", ""):
        with pytest.raises(trash.TrashError):
            trash.purge(run["id"], [bad])
    with pytest.raises(trash.TrashError):
        trash.purge("../../etc")
    with pytest.raises(trash.TrashError):
        trash.restore("20240101-000000-abcd", None)      # well-formed but unknown


def test_staging_root_symlink_is_refused(home, tmp_path):
    real = home / "elsewhere"
    real.mkdir()
    (home / ".cache" / "devprune").mkdir(parents=True)
    paths.trash_root().symlink_to(real)
    with pytest.raises(trash.TrashError):
        trash.create_run()
