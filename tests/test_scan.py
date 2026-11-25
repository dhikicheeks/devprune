import threading

from devprune import paths, scan, state


def mkfile(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def labels(found):
    return {(f["framework"], f["group"], f["label"]) for f in found}


def run_scan(root, depth=6):
    return scan.project_targets(root, depth, threading.Event())


def test_next_project(home):
    p = home / "Developer" / "web"
    mkfile(p / "package.json", '{"dependencies":{"next":"15"}}')
    (p / "node_modules").mkdir()
    (p / ".next").mkdir()
    assert labels(run_scan(home / "Developer")) == {
        ("Next.js", "web", "node_modules/"), ("Next.js", "web", ".next/")}


def test_plain_node_without_next(home):
    p = home / "Developer" / "lib"
    mkfile(p / "package.json", "{}")
    (p / "node_modules").mkdir()
    (p / ".next").mkdir()  # must not count: not a Next.js project
    assert labels(run_scan(home)) == {("Node.js", "lib", "node_modules/")}


def test_laravel_with_frontend(home):
    p = home / "Developer" / "shop"
    mkfile(p / "artisan")
    mkfile(p / "composer.json", "{}")
    mkfile(p / "package.json", "{}")
    for d in ("vendor", "node_modules", "storage/logs", "storage/framework/views", "bootstrap/cache"):
        (p / d).mkdir(parents=True)
    got = labels(run_scan(home))
    assert {g[0] for g in got} == {"Laravel"}  # node_modules belongs to Laravel, not Node.js
    assert {g[2] for g in got} == {"vendor/", "node_modules/", "storage/logs (*.log)",
                                   "storage/framework/views", "bootstrap/cache"}
    modes = {f["label"]: f["mode"] for f in run_scan(home)}
    assert modes["storage/logs (*.log)"] == "contents" and modes["vendor/"] == "dir"


def test_flutter_project(home):
    p = home / "Developer" / "mobile"
    mkfile(p / "pubspec.yaml", "name: m")
    for d in ("build", ".dart_tool", "ios/Pods", "android/.gradle", "android/app/build"):
        (p / d).mkdir(parents=True)
    got = {f["label"] for f in run_scan(home)}
    assert got == {"build/", ".dart_tool/", "ios/Pods", "android/.gradle", "android/app/build"}


def test_does_not_descend_into_node_modules(home):
    p = home / "Developer" / "web"
    mkfile(p / "package.json", "{}")
    inner = p / "node_modules" / "dep"
    mkfile(inner / "package.json", "{}")
    (inner / "node_modules").mkdir()
    found = run_scan(home)
    assert [f["path"] for f in found] == [str(p / "node_modules")]


def test_depth_limit(home):
    p = home / "a" / "b" / "c" / "d" / "proj"
    mkfile(p / "package.json", "{}")
    (p / "node_modules").mkdir()
    assert run_scan(home, depth=3) == []
    assert len(run_scan(home, depth=6)) == 1


def test_hidden_folders_are_skipped(home):
    p = home / ".hidden" / "proj"
    mkfile(p / "package.json", "{}")
    (p / "node_modules").mkdir()
    assert run_scan(home) == []


def test_cancel_stops_scan(home):
    cancel = threading.Event()
    cancel.set()
    mkfile(home / "p" / "package.json", "{}")
    (home / "p" / "node_modules").mkdir()
    assert scan.project_targets(home, 6, cancel) == []
    state.JOB["cancel"] = cancel
    assert scan.do_scan([str(home)], 6) is None


def test_do_scan_hides_tiny_items_and_writes_cache(home, monkeypatch):
    monkeypatch.setattr(scan, "global_targets", lambda: [])
    big = home / "Developer" / "big"
    small = home / "Developer" / "small"
    for p in (big, small):
        mkfile(p / "package.json", "{}")
        (p / "node_modules").mkdir()
    (big / "node_modules" / "f").write_bytes(b"x" * 300_000)
    out = scan.do_scan([str(home / "Developer")], 6)
    assert [i["group"] for i in out] == ["big"]
    assert paths.cache_file().exists()
