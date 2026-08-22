"""Update-notify modes for GitHub-fork installs: release / commit / off.

Covers the fork routing added to ``_check_via_local_git``: a GitHub fork's
passive check consults ``updates.notify`` and compares against the OFFICIAL
repo (release tags or upstream main), while non-GitHub remotes keep the
original origin/main count.
"""

import subprocess as _sp
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import banner


# ---------------------------------------------------------------------------
# Calendar tag parsing
# ---------------------------------------------------------------------------

def test_parse_calendar_release_tag_accepts_calendar_versions():
    assert banner._parse_calendar_release_tag("v2026.8.19") == (2026, 8, 19)
    assert banner._parse_calendar_release_tag("v2026.8.3") == (2026, 8, 3)
    assert banner._parse_calendar_release_tag("v2026.12.31") == (2026, 12, 31)


def test_parse_calendar_release_tag_rejects_non_releases():
    assert banner._parse_calendar_release_tag("v2026.8.19-rc1") is None
    assert banner._parse_calendar_release_tag("v2026.8.19-beta") is None
    assert banner._parse_calendar_release_tag("v0.20.5") is None  # semver, not calendar
    assert banner._parse_calendar_release_tag("2026.8.19") is None  # no v prefix
    assert banner._parse_calendar_release_tag("garbage") is None
    assert banner._parse_calendar_release_tag(None) is None


def test_upstream_release_tags_parses_and_sorts():
    stdout = (
        "abc123\trefs/tags/v2026.8.3\n"
        "def456\trefs/tags/v2026.8.19\n"
        "aaa111\trefs/tags/v2026.8.19^{}\n"  # peel marker, duplicate
        "bbb222\trefs/tags/v2026.8.16\n"
        "ccc333\trefs/tags/v2026.8.19-rc1\n"  # pre-release, ignored
        "ddd444\trefs/tags/v0.20.5\n"  # semver, ignored
        "eee555\trefs/heads/main\n"  # not a tag
    )
    with patch.object(_sp, "run", return_value=SimpleNamespace(returncode=0, stdout=stdout)):
        tags = banner._upstream_release_tags()
    assert tags == [(2026, 8, 3), (2026, 8, 16), (2026, 8, 19)]


def test_upstream_release_tags_empty_on_failure():
    with patch.object(_sp, "run", side_effect=OSError("offline")):
        assert banner._upstream_release_tags() == []
    with patch.object(_sp, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
        assert banner._upstream_release_tags() == []


# ---------------------------------------------------------------------------
# release mode
# ---------------------------------------------------------------------------

def test_check_via_upstream_release_notifies_on_newer_tag():
    with patch.object(
        banner, "_upstream_release_tags",
        return_value=[(2026, 8, 19), (2026, 8, 20)],
    ):
        assert banner._check_via_upstream_release() == banner.UPDATE_RELEASE_AVAILABLE


def test_check_via_upstream_release_quiet_when_current_or_behind():
    with patch.object(banner, "_upstream_release_tags", return_value=[(2026, 8, 19)]):
        assert banner._check_via_upstream_release() == 0
    with patch.object(banner, "_upstream_release_tags", return_value=[(2026, 8, 16)]):
        assert banner._check_via_upstream_release() == 0


def test_check_via_upstream_release_none_on_unknown():
    with patch.object(banner, "_upstream_release_tags", return_value=[]):
        assert banner._check_via_upstream_release() is None
    with patch.object(banner, "RELEASE_DATE", "garbage"):
        with patch.object(banner, "_upstream_release_tags", return_value=[(2026, 8, 20)]):
            assert banner._check_via_upstream_release() is None


# ---------------------------------------------------------------------------
# notify mode resolution
# ---------------------------------------------------------------------------

def test_updates_notify_mode_defaults_and_validates(monkeypatch):
    from hermes_cli import config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda: {})
    assert banner._updates_notify_mode() == "release"
    monkeypatch.setattr(cfg, "load_config", lambda: {"updates": {"notify": "commit"}})
    assert banner._updates_notify_mode() == "commit"
    monkeypatch.setattr(cfg, "load_config", lambda: {"updates": {"notify": "off"}})
    assert banner._updates_notify_mode() == "off"
    # YAML 1.1 parses the bare word `off` as boolean False — must still map.
    monkeypatch.setattr(cfg, "load_config", lambda: {"updates": {"notify": False}})
    assert banner._updates_notify_mode() == "off"
    monkeypatch.setattr(cfg, "load_config", lambda: {"updates": {"notify": "bogus"}})
    assert banner._updates_notify_mode() == "release"

    def _boom():
        raise RuntimeError("cfg")

    monkeypatch.setattr(cfg, "load_config", _boom)
    assert banner._updates_notify_mode() == "release"


# ---------------------------------------------------------------------------
# _check_via_local_git routing
# ---------------------------------------------------------------------------

def test_local_git_routes_fork_by_notify_mode(tmp_path):
    fork_url = "git@github.com:someone/hermes-agent.git"
    with patch.object(banner, "_git_stdout", return_value=fork_url), \
            patch.object(banner, "_updates_notify_mode", return_value="off") as mode, \
            patch.object(banner, "_check_via_upstream_commit") as commit, \
            patch.object(banner, "_check_via_upstream_release") as release:
        assert banner._check_via_local_git(tmp_path) is None
        commit.assert_not_called()
        release.assert_not_called()
        mode.return_value = "commit"
        commit.return_value = 7
        assert banner._check_via_local_git(tmp_path) == 7
        release.assert_not_called()
        mode.return_value = "release"
        release.return_value = banner.UPDATE_RELEASE_AVAILABLE
        assert banner._check_via_local_git(tmp_path) == banner.UPDATE_RELEASE_AVAILABLE
        commit.assert_called_once()


def test_local_git_official_ssh_uses_upstream_commit(tmp_path):
    with patch.object(
        banner, "_git_stdout",
        return_value="git@github.com:NousResearch/hermes-agent.git",
    ), patch.object(
        banner, "_check_via_upstream_commit", return_value=3
    ) as commit:
        assert banner._check_via_local_git(tmp_path) == 3
        commit.assert_called_once_with(tmp_path)


def test_local_git_non_github_remote_keeps_origin_count(tmp_path):
    """Non-GitHub remotes (local paths) keep the fetch-origin path untouched."""

    def _fake_git(args, **_):
        if args == ["remote", "get-url", "origin"]:
            return "file:///tmp/fake-origin.git"
        if args == ["rev-parse", "--is-shallow-repository"]:
            return "false"
        return None

    with patch.object(banner, "_git_stdout", side_effect=_fake_git), \
            patch.object(_sp, "run", return_value=SimpleNamespace(returncode=0, stdout="5\n")):
        assert banner._check_via_local_git(tmp_path) == 5


def test_local_git_https_official_keeps_origin_count(tmp_path):
    """HTTPS official remotes are not SSH; they keep the origin/main count."""

    def _fake_git(args, **_):
        if args == ["remote", "get-url", "origin"]:
            return "https://github.com/NousResearch/hermes-agent.git"
        if args == ["rev-parse", "--is-shallow-repository"]:
            return "false"
        return None

    with patch.object(banner, "_git_stdout", side_effect=_fake_git), \
            patch.object(_sp, "run", return_value=SimpleNamespace(returncode=0, stdout="0\n")):
        assert banner._check_via_local_git(tmp_path) == 0


# ---------------------------------------------------------------------------
# notice rendering
# ---------------------------------------------------------------------------

def test_format_update_notice_release():
    from hermes_cli import config as cfg

    with patch.object(cfg, "recommended_update_command", return_value="hermes update"):
        out = banner._format_update_notice(banner.UPDATE_RELEASE_AVAILABLE)
    assert "new official release available" in out
    assert "hermes update" in out
