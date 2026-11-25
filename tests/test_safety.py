import os

import pytest

from devprune import measure, safety, scan


@pytest.mark.parametrize("rel,expected", [
    ("Developer/app/node_modules", True),
    ("Documents/proj/node_modules", True),       # projects may live under Documents
    (".pub-cache", True),
    (".Trash", True),
    (".gradle/caches", True),
    ("Library/Caches/Homebrew", True),
    ("Library/Logs", True),
    (".", False),                                # home itself
    ("Documents", False),
    ("Desktop", False),
    ("Library", False),
    (".ssh", False),
    (".ssh/keys", False),
    (".gnupg", False),
    ("Library/Keychains", False),
    ("Library/Application Support/x", False),
])
def test_is_safe_inside_home(home, rel, expected):
    assert safety.is_safe(home / rel) is expected


def test_sibling_of_home_is_refused(home):
    # regression: startswith(str(HOME)) used to let /…/home2/… through
    sibling = home.parent / (home.name + "2") / "Developer" / "app" / "node_modules"
    sibling.mkdir(parents=True)
    assert safety.is_safe(sibling) is False


def test_outside_home_is_refused(home):
    assert safety.is_safe("/") is False
    assert safety.is_safe("/etc/hosts") is False
    assert safety.is_safe(home.parent) is False


def test_symlink_escaping_home_refused(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "Developer" / "app" / "node_modules"
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    assert safety.is_safe(link) is False


def test_weird_path_does_not_crash(home):
    assert safety.is_safe("\0bad") is False


def test_builtin_global_targets_pass(home, monkeypatch):
    for d in (".pub-cache", ".Trash", ".gradle/caches", ".npm/_cacache", ".composer/cache",
              "Library/Caches/Homebrew", "Library/Logs", "Library/Developer/Xcode/DerivedData",
              "Library/Developer/CoreSimulator", "Library/Caches/CocoaPods"):
        (home / d).mkdir(parents=True)
    monkeypatch.setattr(measure, "have", lambda c: False)
    targets = scan.global_targets()
    assert len(targets) >= 8
    assert all(safety.is_safe(t["path"]) for t in targets)
