"""Regression tests: hardcoded ANSI in update notices and stderr warnings.

Two bug classes:

1. The deferred update notice rendered rich markup through a bare Rich
   Console, whose stdout writes are mangled by prompt_toolkit's
   patch_stdout ('?[33m' garbage, #2262). The notice must render through
   prompt_toolkit's own renderer (cprint) with ANSI, not raw markup.
2. stderr warnings wrote bare ``\\033`` escapes unconditionally, leaking
   raw escapes into redirected stderr (gateway logs, pipes). They must
   conditionally colorize via the standard colors module, judged on
   stderr's own TTY state.
"""

from io import StringIO
from unittest.mock import patch

import pytest

from hermes_cli import banner
from hermes_cli import colors


# ---------------------------------------------------------------------------
# Update notice rendering
# ---------------------------------------------------------------------------

def test_render_notice_markup_produces_ansi_not_markup():
    out = banner._render_notice_markup("[bold yellow]⚠ update[/]")
    assert "\x1b[" in out
    assert "[bold" not in out and "[yellow" not in out


def test_deferred_notice_routes_through_cprint_not_console():
    """The deferred notice must go through prompt_toolkit's renderer, never
    a bare Console.print (patch_stdout mangles Console stdout writes)."""
    from unittest.mock import MagicMock

    banner._deferred_update_notice_started = False
    banner._update_check_done = MagicMock()
    banner._update_check_done.wait.return_value = True
    banner._update_result = 3

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, **_kw):
            self._target = target

        def start(self):
            self._target()

    with patch.object(banner, "threading", spec=["Thread"]) as _threading:
        _threading.Thread = _ImmediateThread
        with patch.object(banner, "cprint") as _cprint, \
             patch.object(banner, "_format_update_notice", return_value="[bold]3 behind[/]") as _fmt, \
             patch.object(banner, "_render_notice_markup", return_value="\x1b[1m3 behind\x1b[0m") as _render:
            banner._defer_update_notice()

    _cprint.assert_called_once_with("\x1b[1m3 behind\x1b[0m")
    _fmt.assert_called_once_with(3)
    _render.assert_called_once_with("[bold]3 behind[/]")


# ---------------------------------------------------------------------------
# stderr warnings: conditional color, judged on stderr TTY
# ---------------------------------------------------------------------------

def _run_config_warnings(monkeypatch, stderr_tty: bool, no_color: bool):
    from hermes_cli import config as cfg

    class _FakeStream:
        def __init__(self, tty):
            self._tty = tty
            self.buf = StringIO()

        def isatty(self):
            return self._tty

        def write(self, text):
            self.buf.write(text)

    stream = _FakeStream(stderr_tty)
    monkeypatch.setattr("sys.stderr", stream)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)

    issues = [cfg.ConfigIssue(
        severity="error", message="unknown provider", hint="",
    )]
    with patch.object(cfg, "validate_config_structure", return_value=issues):
        cfg.print_config_warnings({})
    return stream.buf.getvalue()


def test_config_warnings_colorized_on_tty_stderr(monkeypatch):
    out = _run_config_warnings(monkeypatch, stderr_tty=True, no_color=False)
    assert "\033[31m" in out  # red ✗ for the error-severity issue
    assert "\033[0m" in out


def test_config_warnings_plain_when_stderr_redirected(monkeypatch):
    out = _run_config_warnings(monkeypatch, stderr_tty=False, no_color=False)
    assert "\033[" not in out
    assert "unknown provider" in out


def test_config_warnings_plain_with_no_color(monkeypatch):
    out = _run_config_warnings(monkeypatch, stderr_tty=True, no_color=True)
    assert "\033[" not in out


def test_should_use_color_stderr_respects_redirect(monkeypatch):
    class _FakeStream:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    monkeypatch.setattr("sys.stderr", _FakeStream(True))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert colors.should_use_color_stderr() is True

    monkeypatch.setattr("sys.stderr", _FakeStream(False))
    assert colors.should_use_color_stderr() is False

    monkeypatch.setattr("sys.stderr", _FakeStream(True))
    monkeypatch.setenv("NO_COLOR", "1")
    assert colors.should_use_color_stderr() is False
