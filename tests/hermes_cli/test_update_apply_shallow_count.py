"""Shallow-checkout guards on the ``hermes update`` apply path (#53479)."""

from pathlib import Path
from unittest.mock import patch

import hermes_cli.update_cmd as update_cmd

SHA_A = "a" * 40
SHA_B = "b" * 40


def _recover(*, shallow: bool, raw_count: int, api_count, target_ref: str = "origin/main"):
    with patch.object(update_cmd, "_is_shallow_checkout", return_value=shallow) as is_shallow, \
         patch.object(update_cmd, "_tip_shas", return_value=(SHA_A, SHA_B)) as tip_shas, \
         patch("hermes_cli.banner._github_compare_behind", return_value=api_count) as compare:
        count = update_cmd._recover_shallow_update_count(
            ["git"], Path("/repo"), raw_count, target_ref
        )
    return count, is_shallow, tip_shas, compare


def test_full_clone_keeps_exact_count_without_compare():
    count, _, tip_shas, compare = _recover(
        shallow=False, raw_count=7, api_count=None
    )
    assert count == 7
    tip_shas.assert_not_called()
    compare.assert_not_called()


def test_shallow_bogus_count_recovers_via_compare_api():
    count, _, _, compare = _recover(shallow=True, raw_count=9980, api_count=12)
    assert count == 12
    compare.assert_called_once_with(SHA_A, SHA_B)


def test_shallow_bogus_count_offline_reports_unknown():
    count, _, _, _ = _recover(shallow=True, raw_count=9980, api_count=None)
    assert count == -1


def test_shallow_local_ahead_treated_as_up_to_date():
    count, _, _, _ = _recover(shallow=True, raw_count=3, api_count=0)
    assert count == 0


def test_zero_count_short_circuits_without_git_or_api():
    count, is_shallow, tip_shas, compare = _recover(
        shallow=True, raw_count=0, api_count=None
    )
    assert count == 0
    is_shallow.assert_not_called()
    tip_shas.assert_not_called()
    compare.assert_not_called()


def test_thin_fork_compares_upstream_tip_not_custom_tip():
    count, _, tip_shas, _ = _recover(
        shallow=True, raw_count=1, api_count=None, target_ref="upstream/main"
    )
    assert count == -1
    tip_shas.assert_called_once_with(["git"], "upstream/main", Path("/repo"))
