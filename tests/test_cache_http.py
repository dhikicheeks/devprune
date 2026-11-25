import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
from conftest import make_item, register

from devprune import cache, paths, scan, server, state


# ------------------------------------------------------------------ cache
def write_cache(items, v=cache.CACHE_VERSION):
    paths.cache_file().parent.mkdir(parents=True, exist_ok=True)
    paths.cache_file().write_text(json.dumps({"v": v, "ts": 1, "items": items}))


def test_cache_roundtrip(home):
    d = home / "Developer" / "a" / "node_modules"
    d.mkdir(parents=True)
    register(make_item(1, d))
    cache.save_cache()
    state.STATE["items"] = {}
    cache.load_cache()
    assert list(state.STATE["items"]) == ["1"]


@pytest.mark.parametrize("override", [
    dict(cmd=["rm", "-rf", "/"]),                  # command not on the allow-list
    dict(path="/etc/hosts"),                       # outside home
    dict(mode="evil"),                             # unknown mode
    dict(guard="rm"),                              # unknown guard
    dict(path="@HOME/Documents"),                  # top-level user folder
    dict(path="@HOME/.ssh"),                       # sensitive
])
def test_hostile_cache_entries_are_filtered(home, override):
    ov = {k: (str(home / v[6:]) if isinstance(v, str) and v.startswith("@HOME/") else v) for k, v in override.items()}
    item = make_item(1, home / "Developer" / "x" / "node_modules")
    item.update(ov)
    write_cache([item])
    cache.load_cache()
    assert state.STATE["items"] == {}


def test_cache_keeps_allowed_command(home):
    write_cache([make_item(1, home / ".pub-cache", cmd=["dart", "pub", "cache", "clean", "--force"])])
    cache.load_cache()
    assert "1" in state.STATE["items"]


def test_old_or_corrupt_cache_is_ignored(home):
    write_cache([make_item(1, home / "Developer" / "x" / "node_modules")], v=1)
    cache.load_cache()
    assert state.STATE["items"] == {}
    paths.cache_file().write_text("{bukan json")
    cache.load_cache()
    assert state.STATE["items"] == {}


def test_vanished_item_is_flagged(home):
    item = make_item(1, home / "Developer" / "gone" / "node_modules", kb=999)
    assert cache.pub(item)["exists"] is False and cache.pub(item)["kb"] == 0


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def srv(home, monkeypatch):
    monkeypatch.setattr(scan, "global_targets", lambda: [])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def call(port, method, path, body=None, token=server.TOKEN, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    if host:
        headers["Host"] = host
    c.request(method, path, json.dumps(body or {}), headers)
    r = c.getresponse()
    return r.status, r.read().decode()


def test_http_missing_or_wrong_token_refused(srv):
    assert call(srv, "POST", "/api/cache", token=None)[0] == 403
    assert call(srv, "POST", "/api/cache", token="salah")[0] == 403


def test_http_foreign_host_refused(srv):
    assert call(srv, "GET", "/", host="evil.example.com")[0] == 403
    assert call(srv, "POST", "/api/cache", host="evil.example.com")[0] == 403


def test_http_index_has_token_and_no_placeholders(srv):
    status, body = call(srv, "GET", "/")
    assert status == 200 and server.TOKEN in body and "__TOKEN__" not in body and "__ROOTS__" not in body


def test_http_scan_then_clean_flow(srv, home):
    d = home / "Developer" / "web"
    d.mkdir(parents=True)
    (d / "package.json").write_text("{}")
    (d / "node_modules").mkdir()
    (d / "node_modules" / "f").write_bytes(b"x" * 300_000)
    assert json.loads(call(srv, "POST", "/api/scan", {"roots": [str(home / "Developer")]})[1])["started"]
    for _ in range(100):
        st = json.loads(call(srv, "POST", "/api/status")[1])
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and len(st["items"]) == 1
    item = st["items"][0]
    call(srv, "POST", "/api/clean", {"ids": [item["id"]], "dry": False})
    for _ in range(100):
        cs = json.loads(call(srv, "POST", "/api/clean_status")[1])
        if cs["state"] != "running":
            break
        time.sleep(0.05)
    assert cs["state"] == "done" and cs["results"][0]["ok"]
    assert not (d / "node_modules").exists()


def test_http_clean_unknown_id_refused(srv, home):
    register()
    call(srv, "POST", "/api/clean", {"ids": ["123"], "dry": False})
    for _ in range(100):
        cs = json.loads(call(srv, "POST", "/api/clean_status")[1])
        if cs["state"] != "running":
            break
        time.sleep(0.05)
    assert cs["results"][0]["ok"] is False


# ------------------------------------------------------------------ HTTP hardening
def raw(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.endheaders(body)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def test_http_foreign_origin_refused_matching_origin_ok(srv):
    host = f"127.0.0.1:{srv}"
    h = {"X-Token": server.TOKEN, "Content-Length": "2"}
    assert raw(srv, "POST", "/api/cache", {**h, "Origin": "http://evil.example"}, b"{}")[0] == 403
    assert raw(srv, "POST", "/api/cache", {**h, "Origin": f"http://{host}"}, b"{}")[0] == 200


def test_http_security_headers_on_every_response(srv):
    for path in ("/", "/static/app.js", "/missing"):
        status, headers, _ = raw(srv, "GET", path)
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"


def test_http_static_whitelist(srv):
    assert raw(srv, "GET", "/static/app.js")[1]["Content-Type"].startswith("text/javascript")
    assert raw(srv, "GET", "/static/style.css")[1]["Content-Type"].startswith("text/css")
    c = http.client.HTTPConnection("127.0.0.1", srv, timeout=10)
    c.request("GET", "/static/logo-mark.png")
    r = c.getresponse()
    data = r.read()
    assert r.status == 200 and r.getheader("Content-Type") == "image/png"
    assert data.startswith(b"\x89PNG")
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    for bad in ("/static/index.html", "/static/../cleaner.py", "/static/%2e%2e/server.py", "/static/"):
        assert raw(srv, "GET", bad)[0] == 404


def test_http_oversized_and_malformed_bodies(srv):
    h = {"X-Token": server.TOKEN}
    assert raw(srv, "POST", "/api/cache", {**h, "Content-Length": "5000000"})[0] == 413
    assert raw(srv, "POST", "/api/cache", {**h, "Content-Length": "5"}, b"{nope")[0] == 400
    assert raw(srv, "POST", "/api/cache", {**h, "Content-Length": "2"}, b"[]")[0] == 400
    assert raw(srv, "POST", "/api/cache", {**h, "Content-Length": "abc"})[0] == 400


def test_http_unknown_route_404(srv):
    assert call(srv, "POST", "/api/nope")[0] == 404


def test_index_is_csp_compliant():
    page = (server.WEB / "index.html").read_text()
    import re
    assert not re.search(r'\sstyle\s*=', page)                      # no inline style attributes
    assert not re.search(r'\son[a-z]+\s*=', page)                   # no inline event handlers
    assert not re.search(r'<script(?![^>]*\ssrc=)[^>]*>', page)     # no inline scripts


def test_roots_are_html_escaped_in_page(home):
    from devprune import config
    config.CURRENT["roots"] = ['/a"><script>alert(1)</script>']
    page = server.render_index()
    assert "<script>alert(1)" not in page and "&quot;" in page


# --- terminal command (launcher) ---------------------------------------------------
import json as _json  # noqa: E402

from devprune import launcher  # noqa: E402


def _cmd(port, action, name):
    code, body = call(port, "POST", f"/api/command_{action}", {"name": name})
    assert code == 200
    return _json.loads(body)


def test_launcher_install_list_remove(home, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    st = launcher.install("bersih")
    assert st["commands"] == ["bersih"]
    f = home / ".local" / "bin" / "bersih"
    text = f.read_text()
    assert f.stat().st_mode & 0o111
    assert launcher.MARKER in text and "-m devprune" in text
    assert launcher.install("bersih")["commands"] == ["bersih"]  # refresh own launcher is allowed
    assert launcher.remove("bersih")["commands"] == []
    assert not f.exists()


BAD_NAMES = ["", "Bersih", "../evil", "a/b", "1abc", "a b", "x" * 33, "-rf", "rm", "cd", None, 5, "a;b", "a$b"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_launcher_rejects_bad_names(home, name):
    with pytest.raises(launcher.LauncherError):
        launcher.install(name)
    assert not (home / ".local").exists()


def test_launcher_never_overwrites_foreign_file(home, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    d = home / ".local" / "bin"
    d.mkdir(parents=True)
    (d / "mine").write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(launcher.LauncherError):
        launcher.install("mine")
    with pytest.raises(launcher.LauncherError):
        launcher.remove("mine")
    assert (d / "mine").read_text() == "#!/bin/sh\necho mine\n"


def test_launcher_refuses_name_of_existing_command(home):
    with pytest.raises(launcher.LauncherError):
        launcher.install("git" if launcher.shutil.which("git") else "ls")


def test_launcher_refuses_symlink_and_limits_count(home, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    d = home / ".local" / "bin"
    d.mkdir(parents=True)
    victim = home / "victim.txt"
    victim.write_text("keep")
    (d / "link").symlink_to(victim)
    with pytest.raises(launcher.LauncherError):
        launcher.install("link")
    with pytest.raises(launcher.LauncherError):
        launcher.remove("link")
    assert victim.read_text() == "keep"
    for i in range(launcher.MAX_LAUNCHERS):
        launcher.install(f"c{i}")
    with pytest.raises(launcher.LauncherError):
        launcher.install("onemore")


def test_launcher_script_runs_the_app(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    launcher.install("bersih")
    out = subprocess.run([str(home / ".local" / "bin" / "bersih"), "--version"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0 and "dev" in out.stdout


def test_http_command_routes_need_token_and_report_errors(srv, home, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    assert call(srv, "POST", "/api/command_set", {"name": "x"}, token=None)[0] == 403
    assert call(srv, "POST", "/api/command_set", {"name": "x"}, host="evil.com")[0] == 403
    assert not (home / ".local").exists()
    assert _cmd(srv, "set", "bersih")["ok"] is True
    assert _json.loads(call(srv, "POST", "/api/command")[1])["commands"] == ["bersih"]
    bad = _cmd(srv, "set", "../x")
    assert bad["ok"] is False and bad["error"]
    assert _cmd(srv, "remove", "bersih")["ok"] is True
    assert _cmd(srv, "remove", "nothere")["ok"] is False


def test_http_clean_status_reports_results_while_running(srv, home):
    state.CJOB.update(state="running", total=3, done=1, current="b (g)", freed_kb=5, dry=False, error="",
                      results=[dict(id="a", ok=True, msg="Done", kb=5)])
    code, body = call(srv, "POST", "/api/clean_status")
    data = _json.loads(body)
    assert code == 200 and data["state"] == "running"
    assert data["results"] == [dict(id="a", ok=True, msg="Done", kb=5)]
    state.CJOB.update(state="idle", results=[])


def test_clean_job_records_plan_and_can_be_acknowledged(srv, home):
    from conftest import wait_clean

    from devprune import clean
    target = home / "Developer" / "app" / ".next"
    target.mkdir(parents=True)
    (target / "a").write_text("x" * 200)
    register(make_item("n1", target, label=".next/", group="app", kb=200))
    assert clean.start_clean(["n1", "ghost"], dry=True)
    wait_clean()
    st = _json.loads(call(srv, "POST", "/api/clean_status")[1])
    assert st["state"] == "done" and st["acked"] is False
    assert st["started"] > 0 and st["finished"] >= st["started"]
    assert [p["id"] for p in st["plan"]] == ["n1", "ghost"]
    assert st["plan"][0] == dict(id="n1", label=".next/", group="app", kb=200, risk="safe")
    assert [r["id"] for r in st["results"]] == ["n1", "ghost"]
    assert call(srv, "POST", "/api/clean_ack", token=None)[0] == 403
    assert _json.loads(call(srv, "POST", "/api/clean_ack")[1]) == {"ok": True}
    assert _json.loads(call(srv, "POST", "/api/clean_status")[1])["acked"] is True
    assert target.exists()  # dry run
