"""Thin-fork update workflow tests.

Covers the thin-fork deploy model: ``main`` is a pure mirror of upstream,
``custom`` carries the fork's fixes and is rebased onto the mirror, and the
fork's own regression tests gate the push. All repos are local (no network):
a bare ``upstream``, a bare ``origin`` (the fork on GitHub), and a fork clone
checked out on ``custom``.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import update_cmd

GIT = ["git"]


def _git(cwd, *args, check=True):
    return subprocess.run(
        GIT + list(args),
        cwd=cwd,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        check=check,
    )


def _init_repo(path: Path):
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main", "-q")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.com")
    return path


def _commit(path: Path, message: str, file: str, content: str):
    (path / file).write_text(content)
    _git(path, "add", file)
    _git(path, "commit", "-q", "-m", message)


def _head(cwd: Path) -> str:
    return _git(cwd, "rev-parse", "HEAD").stdout.strip()


def _remote_head(cwd: Path, remote: Path, ref: str) -> str:
    out = _git(cwd, "ls-remote", str(remote), ref).stdout.strip()
    return out.split()[0] if out else ""


def _make_runner(tmp_path: Path, name: str, exit_code: int) -> Path:
    """A fake test runner that records its argv and exits with *exit_code*."""
    runner = tmp_path / name
    runner.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "Path(str(Path(sys.argv[0]).with_suffix('.log'))).write_text(\n"
        "    '\\n'.join(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    return runner


@pytest.fixture
def fork_world(tmp_path):
    """upstream bare + origin bare + a fork clone on ``custom``.

    The fork starts in sync: main == upstream tip, custom = main + a fix
    commit + a fork-added test file.
    """
    upstream = tmp_path / "upstream.git"
    _git(tmp_path, "init", "--bare", "-q", str(upstream))
    seed = tmp_path / "upstream-seed"
    _init_repo(seed)
    _commit(seed, "upstream c1", "u.txt", "u1\n")
    _commit(seed, "upstream c2", "u.txt", "u2\n")
    _git(seed, "push", "-q", str(upstream), "main")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origin))

    fork = tmp_path / "fork"
    _git(tmp_path, "clone", "-q", str(origin), str(fork))
    _git(fork, "config", "user.name", "Test")
    _git(fork, "config", "user.email", "test@example.com")
    _git(fork, "remote", "add", "upstream", str(upstream))
    _git(fork, "fetch", "-q", "upstream", "main")
    _git(fork, "checkout", "-q", "-B", "main", "upstream/main")
    _git(fork, "checkout", "-q", "-b", "custom")
    _commit(fork, "fork fix", "fix.py", "FIX = True\n")
    (fork / "tests").mkdir()
    (fork / "tests" / "test_fix.py").write_text("def test_fix():\n    assert True\n")
    _git(fork, "add", "tests/test_fix.py")
    _git(fork, "commit", "-q", "-m", "fork test")
    _git(fork, "push", "-q", "-u", "origin", "main")
    _git(fork, "push", "-q", "-u", "origin", "custom")
    return SimpleNamespace(
        upstream=upstream, origin=origin, seed=seed, fork=fork
    )


def _advance_upstream(world, file="u.txt", content="u3\n"):
    _commit(world.seed, "upstream c3", file, content)
    _git(world.seed, "push", "-q", str(world.upstream), "main")
    _git(world.fork, "fetch", "-q", "upstream", "main")


# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------

def test_discover_thin_fork_test_files_lists_fork_added_tests(fork_world):
    files = update_cmd._discover_thin_fork_test_files(GIT, fork_world.fork)
    assert files == ["tests/test_fix.py"]


def test_discover_ignores_missing_and_non_python(fork_world):
    # A test file deleted on disk is not reported.
    (fork_world.fork / "tests" / "test_fix.py").unlink()
    files = update_cmd._discover_thin_fork_test_files(GIT, fork_world.fork)
    assert files == []


# ---------------------------------------------------------------------------
# Mirror sync
# ---------------------------------------------------------------------------

def test_mirror_sync_advances_main_and_pushes(fork_world):
    _advance_upstream(fork_world)
    upstream_tip = _remote_head(fork_world.fork, fork_world.upstream, "main")
    assert _git(fork_world.fork, "rev-parse", "upstream/main").stdout.strip() == upstream_tip

    assert update_cmd._thin_fork_sync_main_mirror(GIT, fork_world.fork) is True

    # Checkout restored to the original branch (custom); main is the mirror.
    assert _git(fork_world.fork, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "custom"
    assert _git(fork_world.fork, "rev-parse", "main").stdout.strip() == upstream_tip
    # Both local main and the fork's GitHub main advanced to upstream.
    assert _remote_head(fork_world.fork, fork_world.origin, "main") == upstream_tip


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------

def test_workflow_rebases_custom_gates_on_tests_and_pushes(
    fork_world, tmp_path
):
    _advance_upstream(fork_world)
    runner = _make_runner(tmp_path, "pass_runner.py", 0)
    upstream_tip = _remote_head(fork_world.fork, fork_world.upstream, "main")
    pre_custom = _head(fork_world.fork)

    assert (
        update_cmd._thin_fork_update_workflow(
            GIT, fork_world.fork, test_runner=[sys.executable, str(runner)]
        )
        is True
    )

    # custom was rebased onto the new upstream main.
    assert _git(fork_world.fork, "rev-parse", "main").stdout.strip() == upstream_tip
    assert _head(fork_world.fork) != pre_custom
    assert _git(fork_world.fork, "merge-base", "--is-ancestor", "main", "custom").returncode == 0
    # The fork's GitHub refs advanced: main mirrored, custom pushed.
    assert _remote_head(fork_world.fork, fork_world.origin, "main") == upstream_tip
    assert _remote_head(fork_world.fork, fork_world.origin, "custom") == _head(fork_world.fork)
    # The test gate ran against the discovered fork test file.
    log = runner.with_suffix(".log").read_text().splitlines()
    assert log == ["tests/test_fix.py"]


def test_workflow_rebase_conflict_aborts_without_pushing(fork_world, tmp_path):
    # Upstream adds its own fix.py — an add/add conflict with the fork's.
    _advance_upstream(fork_world, file="fix.py", content="UPSTREAM = True\n")
    runner = _make_runner(tmp_path, "fail_runner.py", 1)
    pre_custom = _head(fork_world.fork)
    pre_origin_custom = _remote_head(fork_world.fork, fork_world.origin, "custom")

    assert (
        update_cmd._thin_fork_update_workflow(
            GIT, fork_world.fork, test_runner=[sys.executable, str(runner)]
        )
        is False
    )

    # Rebase aborted: custom untouched, nothing pushed, tree clean.
    assert _head(fork_world.fork) == pre_custom
    assert _remote_head(fork_world.fork, fork_world.origin, "custom") == pre_origin_custom
    assert _git(fork_world.fork, "status", "--porcelain").stdout.strip() == ""


def test_workflow_test_failure_blocks_push_but_keeps_local_rebase(
    fork_world, tmp_path
):
    _advance_upstream(fork_world)
    runner = _make_runner(tmp_path, "fail_runner.py", 1)
    pre_custom = _head(fork_world.fork)
    pre_origin_custom = _remote_head(fork_world.fork, fork_world.origin, "custom")

    assert (
        update_cmd._thin_fork_update_workflow(
            GIT, fork_world.fork, test_runner=[sys.executable, str(runner)]
        )
        is False
    )

    # Local custom advanced (rebased) but the failing tests blocked the push.
    assert _head(fork_world.fork) != pre_custom
    assert _remote_head(fork_world.fork, fork_world.origin, "custom") == pre_origin_custom


def test_workflow_with_no_fork_tests_still_syncs(fork_world, tmp_path):
    _git(fork_world.fork, "rm", "-q", "tests/test_fix.py")
    _git(fork_world.fork, "commit", "-q", "-m", "drop test")
    _git(fork_world.fork, "push", "-q", "origin", "custom")
    _advance_upstream(fork_world)
    runner = _make_runner(tmp_path, "pass_runner.py", 0)
    upstream_tip = _remote_head(fork_world.fork, fork_world.upstream, "main")

    assert (
        update_cmd._thin_fork_update_workflow(
            GIT, fork_world.fork, test_runner=[sys.executable, str(runner)]
        )
        is True
    )
    # Runner was never invoked (nothing discovered), but the rebase happened.
    assert not runner.with_suffix(".log").exists()
    assert _remote_head(fork_world.fork, fork_world.origin, "custom") == _head(fork_world.fork)
    assert _remote_head(fork_world.fork, fork_world.origin, "main") == upstream_tip


# ---------------------------------------------------------------------------
# Apply-path integration: _cmd_update_impl in thin-fork mode
# ---------------------------------------------------------------------------

def test_cmd_update_thin_fork_apply_path(fork_world, tmp_path, monkeypatch, capsys):
    """Drive the real update pipeline in thin-fork mode against a sandbox fork.

    Fork origin URL → thin-fork mode; upstream advanced → the pipeline must
    fetch upstream, mirror main, rebase custom, run the (stubbed) test gate,
    and push both branches — before the dependency-install boundary.
    """
    from hermes_cli import main as hermes_main

    class _StopFlow(Exception):
        pass

    _advance_upstream(fork_world)
    fork = fork_world.fork

    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", fork)
    monkeypatch.setattr(hermes_main, "_resolve_update_branch", lambda args: "custom")
    monkeypatch.setattr(hermes_main, "_is_windows", lambda: False)
    monkeypatch.setattr(
        hermes_main, "_get_origin_url",
        lambda *a, **k: "https://github.com/example/hermes-agent.git",
    )
    monkeypatch.setattr(hermes_main, "_discard_lockfile_churn", lambda *a, **k: None)
    monkeypatch.setattr(update_cmd, "_discard_lockfile_churn", lambda *a, **k: None)
    monkeypatch.setattr(update_cmd, "_normalize_managed_eol", lambda *a, **k: None)
    monkeypatch.setattr(hermes_main, "_clear_bytecode_cache", lambda *a, **k: 0)
    monkeypatch.setattr(hermes_main, "_record_bytecode_fingerprint", lambda *a, **k: None)
    monkeypatch.setattr(hermes_main, "_refresh_bootstrap_cache_scripts", lambda *a, **k: None)
    monkeypatch.setattr(hermes_main, "_run_pre_update_backup", lambda *a, **k: None)
    monkeypatch.setattr(hermes_main, "_pause_windows_gateways_for_update", lambda: None)
    monkeypatch.setattr(
        hermes_main, "_resume_windows_gateways_after_update", lambda *a, **k: None
    )
    monkeypatch.setattr(hermes_main, "_capture_active_lazy_features", lambda: [])
    monkeypatch.setattr(hermes_main, "_capture_active_tool_dependencies", lambda: [])
    # The sandbox checkout has no scripts/run_tests.sh — stub the gate (the
    # gate itself is covered by the unit tests above).
    monkeypatch.setattr(update_cmd, "_run_thin_fork_regression_tests", lambda *a, **k: True)
    monkeypatch.setattr(
        hermes_main,
        "_abort_dependency_sync_if_self_locked",
        lambda *a, **k: (_ for _ in ()).throw(_StopFlow()),
    )

    args = SimpleNamespace(branch=None, yes=False, force=False, force_venv=False)
    with pytest.raises(_StopFlow):
        hermes_main.cmd_update(args)

    upstream_tip = _remote_head(fork, fork_world.upstream, "main")
    # main mirrored to upstream and pushed to the fork.
    assert _git(fork, "rev-parse", "main").stdout.strip() == upstream_tip
    assert _remote_head(fork, fork_world.origin, "main") == upstream_tip
    # custom rebased onto the new main and pushed.
    assert _git(fork, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "custom"
    assert _git(fork, "merge-base", "--is-ancestor", "main", "custom").returncode == 0
    assert _remote_head(fork, fork_world.origin, "custom") == _head(fork)
    out = capsys.readouterr().out
    assert "Fork 'custom' synced with upstream" in out


# ---------------------------------------------------------------------------
# Branch resolution
# ---------------------------------------------------------------------------

def test_resolve_update_branch_config_default(monkeypatch):
    from hermes_cli import config as cfg
    from hermes_cli.main import _resolve_update_branch

    monkeypatch.setattr(
        cfg, "load_config", lambda: {"updates": {"branch": "custom"}}
    )
    assert _resolve_update_branch(SimpleNamespace()) == "custom"
    assert _resolve_update_branch(SimpleNamespace(branch="bb")) == "bb"
    assert _resolve_update_branch(SimpleNamespace(branch="  ")) == "custom"
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    assert _resolve_update_branch(SimpleNamespace()) == "main"
    monkeypatch.setattr(
        cfg, "load_config", lambda: {"updates": {"branch": "  "}}
    )
    assert _resolve_update_branch(SimpleNamespace()) == "main"

    def _boom():
        raise RuntimeError("cfg")

    monkeypatch.setattr(cfg, "load_config", _boom)
    assert _resolve_update_branch(SimpleNamespace()) == "main"
