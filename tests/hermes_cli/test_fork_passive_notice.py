"""Fork startup notices track the official source, not the stale fork mirror."""

import subprocess

from hermes_cli import source_check
from hermes_cli import __release_date__


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_fork_passive_release_commit_and_off(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "custom")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "file").write_text("initial", encoding="utf-8")
    _git(root, "add", "file")
    _git(root, "commit", "-qm", "initial")
    _git(root, "remote", "add", "origin", "https://github.com/UMP40/Hermes-Exilium.git")
    head = _git(root, "rev-parse", "HEAD")
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.yaml"
    monkeypatch.setattr(source_check, "_unsupported_reason", lambda *a, **kw: None)
    real_git_run = source_check._git_run
    year = int(__release_date__.split(".")[0]) + 1

    def git_run(args, **kwargs):
        if args[:2] == ["ls-remote", "--tags"]:
            return subprocess.CompletedProcess(args, 0, f"{head}\trefs/tags/v{year}.1.1\n")
        return real_git_run(args, **kwargs)

    monkeypatch.setattr(source_check, "_git_run", git_run)
    config.write_text("updates:\n  notify: release\n", encoding="utf-8")
    release = source_check.check_for_updates(install_root=root, home=home, channel="main", passive=True)
    assert release["behind"] == source_check.UPDATE_RELEASE_AVAILABLE
    assert release["updateAvailable"] is True

    # The same checkout and cache must not retain the release result after mode changes.
    config.write_text("updates:\n  notify: commit\n", encoding="utf-8")
    monkeypatch.setattr(source_check, "_branch_tip", lambda *a, **kw: ("f" * 40, False, None))
    monkeypatch.setattr(source_check, "_github_compare", lambda current, target, repository: {
        "ahead_by": 3, "commits": [],
    } if current == head and target == "f" * 40 and repository == source_check.OFFICIAL_REPOSITORY else None)
    commit = source_check.check_for_updates(install_root=root, home=home, channel="main", passive=True)
    assert commit["behind"] == 3
    assert commit["targetSha"] == "f" * 40

    config.write_text("updates:\n  notify: 'off'\n", encoding="utf-8")
    disabled = source_check.check_for_updates(install_root=root, home=home, channel="main", passive=True)
    assert disabled["reason"] == "disabled"
    assert disabled["behind"] is None
