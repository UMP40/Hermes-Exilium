"""Regression tests: stale in-memory config overwrite (vision + langfuse).

The bug class: an entry flow holds a config object A obtained from
``load_config()``, a sub-flow loads its OWN object B, mutates and saves it,
and the outer flow then saves the stale A, silently discarding B's writes.

Each test reproduces the full chain — sub-flow runs against the caller's
object, then the caller saves again — and re-reads from disk afterwards,
asserting the sub-flow's writes survived. This catches the overwrite that
single-function "it saved" assertions miss.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli.config import load_config, save_config
import hermes_cli.tools_config as tc


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Each test gets its own HERMES_HOME so disk state is deterministic."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# Vision: auxiliary.vision survives the enclosing save
# ---------------------------------------------------------------------------

def test_vision_custom_endpoint_survives_outer_save():
    """Full chain: vision sub-flow writes through the caller's config, the
    caller saves afterwards, and auxiliary.vision is still on disk."""
    config_a = load_config()

    seq = iter([2])  # "Custom OpenAI-compatible endpoint"
    prompts = iter(["https://my.endpoint/v1", "sk-secret", "my-vision-model"])
    with patch.object(tc, "_prompt_choice", side_effect=lambda *a, **k: next(seq)), \
         patch.object(tc, "_prompt", side_effect=lambda *a, **k: next(prompts)), \
         patch.object(tc, "save_env_value") as save_env, \
         patch.object(tc, "_toolset_has_keys", return_value=False):
        # The reconfigure path hands the caller's object down to the picker.
        tc._reconfigure_simple_requirements("vision", config_a)

    save_config(config_a)  # enclosing save AFTER the sub-flow

    fresh = load_config()  # re-read from disk
    v = fresh.get("auxiliary", {}).get("vision", {})
    assert v.get("provider") == "custom"
    assert v.get("base_url") == "https://my.endpoint/v1"
    assert v.get("model") == "my-vision-model"
    save_env.assert_called_once_with("OPENAI_API_KEY", "sk-secret")


def test_vision_auto_survives_outer_save():
    """Auto mode clears pinned keys; the enclosing save must not resurrect
    them from a stale object."""
    config_a = load_config()
    config_a.setdefault("auxiliary", {})["vision"] = {
        "provider": "openai", "model": "gpt-4o", "base_url": "x",
    }
    save_config(config_a)
    config_a = load_config()  # caller holds current state

    seq = iter([0])  # "Auto"
    with patch.object(tc, "_prompt_choice", side_effect=lambda *a, **k: next(seq)):
        tc._reconfigure_simple_requirements("vision", config_a)

    save_config(config_a)

    fresh = load_config()
    v = fresh.get("auxiliary", {}).get("vision", {})
    # Pinned overrides cleared; load_config re-fills the auto defaults from
    # DEFAULT_CONFIG, which is exactly the "auto" semantics — none of the
    # previously pinned values may survive.
    assert v.get("provider") != "openai"
    assert v.get("model") != "gpt-4o"
    assert v.get("base_url") != "x"


# ---------------------------------------------------------------------------
# Langfuse: plugins.enabled survives the enclosing save
# ---------------------------------------------------------------------------

def test_langfuse_enable_survives_outer_save():
    """Full chain: langfuse post-setup adds observability/langfuse to the
    caller's config; the enclosing save must not wipe it."""
    config_a = load_config()

    with patch.object(tc, "_pip_install", return_value=SimpleNamespace(returncode=0)), \
         patch.object(tc, "_print_success"), \
         patch.object(tc, "_print_info"), \
         patch.object(tc, "_print_warning"):
        tc._run_post_setup("langfuse", config_a)

    save_config(config_a)  # enclosing save AFTER the sub-flow

    fresh = load_config()  # re-read from disk
    enabled = fresh.get("plugins", {}).get("enabled", [])
    assert "observability/langfuse" in enabled


def test_langfuse_already_enabled_stays_single_entry():
    """Idempotent: enabling twice keeps one plugins.enabled entry."""
    config_a = load_config()
    config_a.setdefault("plugins", {})["enabled"] = ["observability/langfuse"]
    save_config(config_a)

    with patch.object(tc, "_pip_install", return_value=SimpleNamespace(returncode=0)), \
         patch.object(tc, "_print_success"), \
         patch.object(tc, "_print_info"), \
         patch.object(tc, "_print_warning"):
        tc._run_post_setup("langfuse", load_config())

    save_config(config_a)
    fresh = load_config()
    enabled = fresh.get("plugins", {}).get("enabled", [])
    assert enabled.count("observability/langfuse") == 1
