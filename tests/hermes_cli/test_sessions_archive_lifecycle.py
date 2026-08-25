"""CLI archive lifecycle: list/browse archive filters and unarchive.

Covers the archive-only gap fix — archiving was one-way on the CLI:
``hermes sessions archive`` hid a session with no CLI way to list it
(``--archived`` / ``--all``), no unarchive command, and an archived+hidden
session became unreachable from every surface. The state layer already
supported all of it; these tests pin the CLI surface and the query
semantics.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli import sessions_cmd as sc
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# cmd_sessions with a real SessionDB (return-code contract)
# ---------------------------------------------------------------------------

import hermes_state as _hs

_seq = [0]


def _real_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # cmd_sessions opens SessionDB() with the import-time DEFAULT_DB_PATH;
    # point it at the same temp file so the CLI acts on this store.
    monkeypatch.setattr(_hs, "DEFAULT_DB_PATH", tmp_path / "state.db")
    db = SessionDB(tmp_path / "state.db")
    sid = db.create_session("s1", source="cli")
    return db, sid


def _args(action, **kw):
    from types import SimpleNamespace

    return SimpleNamespace(sessions_action=action, **kw)


def test_unarchive_missing_returns_1(tmp_path, monkeypatch, capsys):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        rc = sc.cmd_sessions(_args("unarchive", session_id="nope_xyz"))
    finally:
        db.close()
    assert rc == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_unarchive_success_flips_archived_flag(tmp_path, monkeypatch, capsys):
    db, sid = _real_db(tmp_path, monkeypatch)
    try:
        db.set_session_archived(sid, True)
        assert db.get_session(sid)["archived"] == 1

        rc = sc.cmd_sessions(_args("unarchive", session_id=sid))
        assert rc in (0, None)
        assert db.get_session(sid)["archived"] == 0
    finally:
        db.close()
    assert "Unarchived session" in capsys.readouterr().out


def test_unarchive_accepts_unique_prefix(tmp_path, monkeypatch, capsys):
    db, sid = _real_db(tmp_path, monkeypatch)
    try:
        db.set_session_archived(sid, True)
        prefix = sid[:8]
        rc = sc.cmd_sessions(_args("unarchive", session_id=prefix))
        assert rc in (0, None)
        assert db.get_session(sid)["archived"] == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# list --archived / --all query semantics against a real DB
# ---------------------------------------------------------------------------

def _make_session(db, *, archived=False, hidden=False, source="cli", tag=None):
    _seq[0] += 1
    sid = db.create_session(tag or f"s{id(db)}-{_seq[0]}", source=source)
    if archived or hidden:
        db._conn.execute(
            "UPDATE sessions SET archived = ?, hidden = ? WHERE id = ?",
            (1 if archived else 0, 1 if hidden else 0, sid),
        )
        db._conn.commit()
    return sid


def _list_rows(db, *, archived=None, all_=False):
    kwargs = {"source": None, "exclude_sources": None, "limit": 100}
    if archived is True:
        kwargs.update(archived_only=True, include_archived=False, include_hidden=True)
    elif archived is False:
        kwargs.update(archived_only=False, include_archived=False, include_hidden=False)
    else:
        kwargs.update(include_archived=all_)
    return db.list_sessions_rich(**kwargs)


def test_list_archived_only_includes_hidden_archived(tmp_path, monkeypatch):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        _make_session(db)
        _make_session(db, archived=True)
        hidden_archived = _make_session(db, archived=True, hidden=True)

        rows = _list_rows(db, archived=True)
        ids = [r["id"] for r in rows]
        assert len(rows) == 2  # archived + archived&hidden, NOT the plain one
        assert hidden_archived in ids
        assert all(r["archived"] == 1 for r in rows)
    finally:
        db.close()


def test_list_all_includes_archived_but_not_plain_hidden(tmp_path, monkeypatch):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        active = _make_session(db)
        archived = _make_session(db, archived=True)
        hidden = _make_session(db, hidden=True)

        rows = _list_rows(db, all_=True)
        ids = [r["id"] for r in rows]
        assert active in ids and archived in ids
        assert hidden not in ids  # --all must not surface plain hidden rows
    finally:
        db.close()


def test_list_default_excludes_archived(tmp_path, monkeypatch):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        active = _make_session(db)
        archived = _make_session(db, archived=True)
        rows = _list_rows(db)
        ids = [r["id"] for r in rows]
        assert active in ids
        assert archived not in ids
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Full CLI parse path (main argv → list flags reach the DB query)
# ---------------------------------------------------------------------------

def _run_cli(monkeypatch, capsys, argv_tail, db):
    import hermes_cli.main as hm

    captured = {}

    class _FakeDB:
        def __init__(self, real):
            self._real = real
            self.closed = False

        def close(self):
            self.closed = True

        def list_sessions_rich(self, **kwargs):
            captured["kwargs"] = kwargs
            return []

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr("sys.argv", ["hermes"] + argv_tail)
    monkeypatch.setattr("hermes_state.SessionDB", lambda *a, **k: _FakeDB(db))
    try:
        hm.main()
    except SystemExit as exc:
        return exc.code, captured
    return 0, captured


def test_sessions_list_archived_passes_archived_only_and_hidden(
    tmp_path, monkeypatch, capsys
):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        code, captured = _run_cli(
            monkeypatch, capsys, ["sessions", "list", "--archived"], db
        )
    finally:
        db.close()
    assert code == 0
    kw = captured["kwargs"]
    assert kw["archived_only"] is True
    assert kw["include_hidden"] is True


def test_sessions_list_all_passes_include_archived_without_hidden(
    tmp_path, monkeypatch, capsys
):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        code, captured = _run_cli(
            monkeypatch, capsys, ["sessions", "list", "--all"], db
        )
    finally:
        db.close()
    assert code == 0
    kw = captured["kwargs"]
    assert kw["include_archived"] is True
    assert "include_hidden" not in kw or kw["include_hidden"] is False


# ---------------------------------------------------------------------------
# Help discoverability + argparse contracts (fork-added archive surface)
# ---------------------------------------------------------------------------


def _run_argv(monkeypatch, argv_tail):
    import hermes_cli.main as hm

    monkeypatch.setattr("sys.argv", ["hermes"] + argv_tail)
    try:
        hm.main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_sessions_group_help_lists_unarchive(monkeypatch, capsys):
    code = _run_argv(monkeypatch, ["sessions", "-h"])
    assert code == 0
    assert "unarchive" in capsys.readouterr().out


def test_sessions_list_help_documents_archive_flags(monkeypatch, capsys):
    code = _run_argv(monkeypatch, ["sessions", "list", "-h"])
    assert code == 0
    out = capsys.readouterr().out
    assert "--archived" in out and "--all" in out


def test_sessions_browse_help_documents_archive_flags(monkeypatch, capsys):
    code = _run_argv(monkeypatch, ["sessions", "browse", "-h"])
    assert code == 0
    out = capsys.readouterr().out
    assert "--archived" in out and "--all" in out



def test_sessions_list_archived_all_mutually_exclusive(monkeypatch, capsys):
    code = _run_argv(monkeypatch, ["sessions", "list", "--archived", "--all"])
    assert code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_sessions_browse_archived_all_mutually_exclusive(monkeypatch, capsys):
    code = _run_argv(monkeypatch, ["sessions", "browse", "--archived", "--all"])
    assert code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_unarchive_full_argv_prefix_flips_flag(tmp_path, monkeypatch, capsys):
    db, _ = _real_db(tmp_path, monkeypatch)
    sid = db.create_session("arch-argv-1", source="cli")
    db.set_session_archived(sid, True)
    try:
        code, _ = _run_cli(
            monkeypatch, capsys, ["sessions", "unarchive", "arch-argv"], db
        )
        assert code == 0
        assert db.get_session(sid)["archived"] == 0
    finally:
        db.close()
    assert "Unarchived session" in capsys.readouterr().out


def test_unarchive_full_argv_missing_returns_1(tmp_path, monkeypatch, capsys):
    db, _ = _real_db(tmp_path, monkeypatch)
    try:
        code, _ = _run_cli(
            monkeypatch, capsys, ["sessions", "unarchive", "nope_xyz"], db
        )
    finally:
        db.close()
    assert code == 1
    assert "not found" in capsys.readouterr().out.lower()
