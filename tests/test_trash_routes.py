import json
import time

import pytest
from conftest import make_item, register, wait_clean
from test_cache_http import call, srv  # noqa: F401  (srv is a fixture)
from test_clean import populated

from devprune import cache, clean, cli, paths, server, state, trash


def stage(home, name, kb=100, parent="app"):
    d = populated(home / "Developer" / parent / name)
    register(*state.STATE["items"].values(), make_item(f"{parent}-{name}", d, kb=kb, label=name, group=parent))
    out = clean.do_clean([f"{parent}-{name}"], False, None, False)
    assert out["results"][0]["staged"]
    return d, out["run"]


def wait_job():
    wait_clean()
    return server._clean_status({})


# ---- read routes

def test_summary_and_entries_routes(home):
    _, run = stage(home, "node_modules", kb=500)
    s = server._trash_summary({})
    assert s["ok"] and s["count"] == 1 and s["kb"] == 500 and s["runs"][0]["id"] == run
    e = server.ROUTES["/api/trash_entries"]({"run": run})
    assert e["ok"] and e["items"][0]["label"] == "node_modules" and e["items"][0]["restorable"]
    json.dumps(s)
    json.dumps(e)                                   # everything the routes return must be JSON-safe


def test_conflicts_route_lists_what_a_restore_would_replace(home):
    d, run = stage(home, "node_modules")
    d.mkdir()
    (d / "new").write_text("x")
    r = server.ROUTES["/api/trash_conflicts"]({"run": run})
    assert r["ok"] and [c["path"] for c in r["items"]] == [str(d.resolve())]
    assert server.ROUTES["/api/trash_conflicts"]({})["items"][0]["run"] == run     # no run: all runs


# ---- empty

def test_empty_job_frees_space_and_removes_run(home):
    _, run = stage(home, "node_modules", kb=500)
    assert server.ROUTES["/api/trash_empty"]({"run": run})["started"]
    st = wait_job()
    assert st["state"] == "done" and st["kind"] == "empty" and st["freed_kb"] == 500
    assert st["results"][0]["ok"] and st["results"][0]["id"].startswith(run + "/")
    assert st["plan"][0]["label"] == "node_modules" and not (paths.trash_root() / run).exists()


def test_empty_without_run_empties_every_run(home):
    _, r1 = stage(home, "node_modules", kb=10)
    time.sleep(1.1)                                 # run ids carry a one-second timestamp
    _, r2 = stage(home, "build", kb=20, parent="other")
    assert r1 != r2
    assert server.ROUTES["/api/trash_empty"]({})["started"]
    st = wait_job()
    assert st["freed_kb"] == 30 and trash.summary()["count"] == 0


def test_empty_stop_leaves_the_rest_in_staging(home, monkeypatch):
    for k in range(3):
        stage(home, f"n{k}", kb=10, parent=f"p{k}")
        time.sleep(0.05)
    real = trash.purge
    calls = []

    def purge_then_stop(*a, **k):
        calls.append(1)
        out = real(*a, **k)
        state.CJOB["stop"].set()
        return out
    monkeypatch.setattr(trash, "purge", purge_then_stop)
    assert server.ROUTES["/api/trash_empty"]({})["started"]
    st = wait_job()
    assert st["state"] == "stopped" and len(calls) == 1 and trash.summary()["count"] >= 2


# ---- restore

def test_restore_job_puts_items_back_and_asks_for_rescan(home):
    d, run = stage(home, "node_modules")
    assert server.ROUTES["/api/trash_restore"]({"run": run})["started"]
    st = wait_job()
    assert st["kind"] == "restore" and st["state"] == "done" and st["rescan"] is True
    assert (d / "sub" / "g").exists() and trash.summary()["count"] == 0


def test_restore_conflict_then_replace_after_the_alert(home):
    d, run = stage(home, "node_modules")
    d.mkdir()
    (d / "new").write_text("regenerated")
    assert server.ROUTES["/api/trash_restore"]({"run": run})["started"]
    st = wait_job()
    assert not st["results"][0]["ok"] and st["results"][0]["conflict"] and st["rescan"] is False
    assert (d / "new").exists() and trash.summary()["count"] == 1
    assert server.ROUTES["/api/trash_restore"]({"run": run, "replace": True})["started"]
    st = wait_job()
    assert st["results"][0]["ok"] and not (d / "new").exists() and (d / "sub" / "g").exists()


def test_replace_must_be_a_real_true(home):
    d, run = stage(home, "node_modules")
    d.mkdir()
    (d / "new").write_text("regenerated")
    assert server.ROUTES["/api/trash_restore"]({"run": run, "replace": "yes"})["started"]
    wait_job()
    assert (d / "new").exists()                     # "yes" is not True: nothing was replaced


# ---- validation (SR-15) and exclusivity

@pytest.mark.parametrize("body", [
    {"run": "../../etc"}, {"run": 5}, {"run": "20240101-000000-abcd"},
    {"ids": ["001"]},                               # ids without a run
    {"run": "@RUN", "ids": ["../.."]}, {"run": "@RUN", "ids": "001"}, {"run": "@RUN", "ids": [1]},
    {"run": "@RUN", "ids": ["999"]},
])
def test_bad_ids_never_start_a_job_or_touch_anything(home, body):
    d, run = stage(home, "node_modules")
    body = {k: (run if v == "@RUN" else v) for k, v in body.items()}
    for route in ("/api/trash_empty", "/api/trash_restore"):
        r = server.ROUTES[route](body)
        assert r["started"] is False and r["error"]
    assert trash.summary()["count"] == 1 and not d.exists()


def test_nothing_to_do_and_busy(home, monkeypatch):
    assert server.ROUTES["/api/trash_empty"]({}) == {"started": False, "error": "Nothing to do."}
    _, run = stage(home, "node_modules")
    monkeypatch.setitem(state.JOB, "state", "running")
    r = server.ROUTES["/api/trash_empty"]({"run": run})
    assert r["started"] is False and "running" in r["error"] and trash.summary()["count"] == 1
    monkeypatch.setitem(state.JOB, "state", "idle")


def test_orphan_run_can_be_emptied_but_not_restored(home):
    _, run = stage(home, "node_modules")
    (paths.trash_root() / run / trash.MANIFEST).write_text("{broken")
    e = server.ROUTES["/api/trash_entries"]({"run": run})["items"][0]
    assert e["restorable"] is False and e["problem"]
    server.ROUTES["/api/trash_restore"]({"run": run})
    st = wait_job()
    assert not st["results"][0]["ok"] and trash.summary()["count"] == 1
    server.ROUTES["/api/trash_empty"]({"run": run})
    assert wait_job()["results"][0]["ok"] and trash.summary()["count"] == 0


# ---- HTTP

def test_http_trash_routes_need_the_token(srv):  # noqa: F811
    for path in ("/api/trash", "/api/trash_entries", "/api/trash_conflicts", "/api/trash_restore",
                 "/api/trash_empty"):
        assert call(srv, "POST", path, token=None)[0] == 403
        assert call(srv, "POST", path, host="evil.example.com")[0] == 403


def test_http_trash_roundtrip(srv, home):  # noqa: F811
    d, run = stage(home, "node_modules", kb=500)
    status, body = call(srv, "POST", "/api/trash")
    assert status == 200 and json.loads(body)["count"] == 1
    status, body = call(srv, "POST", "/api/trash_empty", {"run": "../x"})
    assert json.loads(body)["started"] is False
    status, body = call(srv, "POST", "/api/trash_restore", {"run": run})
    assert json.loads(body)["started"] is True
    wait_clean()
    assert (d / "sub" / "g").exists()


def test_pub_and_cache_unaffected_by_staged_items(home):
    d, _ = stage(home, "node_modules")
    cache.save_cache()
    assert "app-node_modules" not in state.STATE["items"] and not d.exists()


# ---- startup

def test_start_repairs_journals_only_in_the_process_that_owns_the_port(home, monkeypatch):
    seen = []
    monkeypatch.setattr(trash, "reconcile", lambda: seen.append(1) or 0)

    class Srv:
        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "make_server", lambda port: Srv())
    assert cli.main(["--no-browser"]) == 0 and seen == [1]

    def taken(port):
        raise OSError("in use")
    monkeypatch.setattr(server, "make_server", taken)
    assert cli.main(["--no-browser"]) == 0 and seen == [1]       # second instance did not reconcile


def test_start_survives_a_damaged_staging_folder(home, monkeypatch, capsys):
    def boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(trash, "reconcile", boom)

    class Srv:
        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "make_server", lambda port: Srv())
    assert cli.main(["--no-browser"]) == 0
    assert "staging folder" in capsys.readouterr().out



def test_staged_data_is_never_a_clean_target_even_from_a_forged_cache(home):
    d, run = stage(home, "node_modules")
    forged = paths.trash_root() / run / "001" / "node_modules"
    assert forged.exists()
    item = make_item("forged", forged)
    paths.cache_file().parent.mkdir(parents=True, exist_ok=True)
    paths.cache_file().write_text(json.dumps({"v": cache.CACHE_VERSION, "ts": 1, "items": [item]}))
    state.STATE["items"] = {}
    cache.load_cache()
    assert "forged" not in state.STATE["items"]                     # dropped by the cache validation
    register(item)                                                  # and refused even if it got in
    r = clean.do_clean(["forged"], False)["results"][0]
    assert not r["ok"] and "unsafe" in r["msg"] and forged.exists()
