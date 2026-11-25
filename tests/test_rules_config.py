import json
import threading

import pytest

from devprune import config, measure, paths, rules, safety, scan


def valid_rule(**over):
    r = {"framework": "Rust", "detect": {"files_all": ["Cargo.toml"]},
         "targets": [{"path": "target", "label": "target/", "risk": "safe", "note": "Cargo build output"}]}
    r.update(over)
    return r


# ------------------------------------------------------------------ built-in data
def test_builtin_project_rules_are_valid():
    for r in rules.project_rules():
        assert r["targets"] and all(t["risk"] in rules.RISKS and t["mode"] in rules.MODES for t in r["targets"])


def test_builtin_global_rules_are_consistent():
    for r in rules.global_rules():
        assert r["risk"] in rules.RISKS and r["note"] and r["label"] and r["group"]
        paths_ = r["path"] if isinstance(r["path"], list) else [r["path"]]
        assert all(p.startswith("~/") for p in paths_)
        assert r.get("guard") in safety.ALLOWED_GUARDS
        if r.get("cmd"):
            assert r.get("requires") and isinstance(r.get("fallback_delete"), bool)
            assert tuple(r["cmd"]) in rules.builtin_commands()


def test_every_builtin_global_path_passes_is_safe(home):
    for r in rules.global_rules():
        for p in (r["path"] if isinstance(r["path"], list) else [r["path"]]):
            assert safety.is_safe(home / p[2:]), p


# ------------------------------------------------------------------ validation
def test_valid_user_rule_is_accepted():
    assert rules.validate_project_rule(valid_rule())["framework"] == "Rust"


@pytest.mark.parametrize("bad", [
    valid_rule(framework=""),
    valid_rule(detect={}),
    valid_rule(detect={"files_all": ["Cargo.toml"], "extra": 1}),
    valid_rule(detect={"any_of": [{"file": "a", "file_prefix": "b"}]}),
    valid_rule(targets=[]),
    valid_rule(targets=[{"path": "/etc"}]),
    valid_rule(targets=[{"path": "../outside"}]),
    valid_rule(targets=[{"path": "a/../../b"}]),
    valid_rule(targets=[{"path": "target", "mode": "shred"}]),
    valid_rule(targets=[{"path": "target", "risk": "yolo"}]),
    valid_rule(targets=[{"path": "target", "cmd": ["rm", "-rf", "/"]}]),
    valid_rule(cmd=["rm", "-rf", "/"]),
    valid_rule(prune_dirs=["a/b"]),
    "not an object",
])
def test_invalid_user_rules_are_rejected(bad):
    with pytest.raises(rules.RuleError):
        rules.validate_project_rule(bad)


def test_matches_semantics():
    nxt = next(r for r in rules.project_rules() if r["framework"] == "Next.js")
    assert rules.matches(nxt, {"package.json", "next.config.mjs"})
    assert rules.matches(nxt, {"package.json"}, '{"dependencies":{"next":"15"}}')
    assert not rules.matches(nxt, {"package.json"}, "{}")
    assert not rules.matches(nxt, {"next.config.mjs"})  # needs package.json too


# ------------------------------------------------------------------ config
def write_config(home, data):
    f = paths.config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(data if isinstance(data, str) else json.dumps(data))


def test_no_config_gives_defaults(home):
    cfg = config.load()
    assert cfg["roots"] == [] and cfg["exclude"] == [] and cfg["project_rules"] == []


def test_config_roots_exclude_and_rules(home):
    write_config(home, {"roots": ["~/Dev"], "exclude": ["~/Dev/keep"], "project_rules": [valid_rule()]})
    cfg = config.load()
    assert cfg["roots"] == [str(home / "Dev")]
    assert cfg["project_rules"][0]["framework"] == "Rust"
    assert config.default_roots() == [str(home / "Dev")]
    assert not safety.is_safe(home / "Dev" / "keep" / "node_modules")   # inside an excluded path
    assert not safety.is_safe(home / "Dev")                              # contains an excluded path
    assert safety.is_safe(home / "Dev" / "other" / "node_modules")


def test_corrupt_config_is_ignored_with_warning(home, capsys):
    write_config(home, "{not json")
    assert config.load()["roots"] == []
    assert "ignoring unreadable config" in capsys.readouterr().err


def test_invalid_rule_in_config_is_skipped_with_warning(home, capsys):
    write_config(home, {"project_rules": [valid_rule(targets=[{"path": "/etc"}]), valid_rule()]})
    cfg = config.load()
    assert len(cfg["project_rules"]) == 1
    assert "project_rules[0] ignored" in capsys.readouterr().err


def test_default_roots_fall_back_to_common_folders(home):
    (home / "Developer").mkdir()
    (home / "Documents").mkdir()
    assert config.default_roots() == [str(home / "Developer"), str(home / "Documents")]


# ------------------------------------------------------------------ scanning with config
def test_user_rule_finds_rust_target(home):
    proj = home / "Developer" / "crab"
    (proj / "target").mkdir(parents=True)
    (proj / "Cargo.toml").write_text("[package]")
    write_config(home, {"project_rules": [valid_rule()]})
    config.load()
    found = scan.project_targets(home, 6, threading.Event())
    assert [(f["framework"], f["label"]) for f in found] == [("Rust", "target/")]


def test_excluded_project_is_not_scanned(home):
    for name in ("keep", "other"):
        p = home / "Developer" / name
        (p / "node_modules").mkdir(parents=True)
        (p / "package.json").write_text("{}")
    write_config(home, {"exclude": ["~/Developer/keep"]})
    config.load()
    found = scan.project_targets(home, 6, threading.Event())
    assert [f["group"] for f in found] == ["other"]


def test_do_scan_drops_excluded_targets(home, monkeypatch):
    monkeypatch.setattr(scan, "global_targets", lambda: [])
    p = home / "Developer" / "web"
    (p / "node_modules").mkdir(parents=True)
    (p / "node_modules" / "f").write_bytes(b"x" * 300_000)
    (p / "package.json").write_text("{}")
    write_config(home, {"exclude": ["~/Developer/web/node_modules"]})
    config.load()
    assert scan.do_scan([str(home)], 6) == []


# ------------------------------------------------------------------ global target resolution
@pytest.fixture
def fake_tools(home, monkeypatch):
    present = set()
    monkeypatch.setattr(measure, "have", lambda c: c in present)
    monkeypatch.setattr(measure, "run_out", lambda cmd, timeout=15: "")
    return present


def by_label(targets):
    return {t["label"]: t for t in targets}


def test_tool_missing_with_fallback_deletes_folder_directly(home, fake_tools):
    (home / ".npm" / "_cacache").mkdir(parents=True)
    t = by_label(scan.global_targets())["npm cache"]
    assert t["cmd"] is None and t["mode"] == "dir"


def test_tool_present_uses_official_command(home, fake_tools):
    (home / ".npm" / "_cacache").mkdir(parents=True)
    fake_tools.add("npm")
    assert by_label(scan.global_targets())["npm cache"]["cmd"] == ["npm", "cache", "clean", "--force"]


def test_tool_missing_without_fallback_hides_item(home, fake_tools):
    (home / "Library" / "Developer" / "CoreSimulator").mkdir(parents=True)
    assert "iOS Simulator (unavailable)" not in by_label(scan.global_targets())
    fake_tools.add("xcrun")
    assert "iOS Simulator (unavailable)" in by_label(scan.global_targets())


def test_composer_picks_the_existing_candidate(home, fake_tools):
    (home / "Library" / "Caches" / "composer").mkdir(parents=True)
    t = by_label(scan.global_targets())["Composer cache"]
    assert t["path"] == str(home / "Library" / "Caches" / "composer")


def test_path_from_command_is_honoured(home, fake_tools, monkeypatch):
    custom = home / "pnpm-store-elsewhere"
    custom.mkdir()
    fake_tools.add("pnpm")
    monkeypatch.setattr(measure, "run_out", lambda cmd, timeout=15: str(custom))
    assert by_label(scan.global_targets())["pnpm store (prune)"]["path"] == str(custom)


def test_gradle_item_carries_guard(home, fake_tools):
    (home / ".gradle" / "caches").mkdir(parents=True)
    assert by_label(scan.global_targets())["Gradle caches"]["guard"] == "gradle"
