"""Regression tests: stale in-memory config overwrite in the `hermes tools` flow.

The bug class: an entry flow holds a config object A obtained from
``load_config()``, a sub-flow loads its OWN object B, mutates and saves it, and
the outer flow then saves the stale A, silently discarding B's writes.

``_configure_vision_backend`` used to re-load config internally, so the vision
backend it had just written was wiped by the enclosing toolset save. Each test
runs the real chain — sub-flow on the caller's object, then the enclosing
platform write and final save — and re-reads from disk afterwards, so it catches
the overwrite that single-function "it saved" assertions miss.
"""

from unittest.mock import patch

import pytest

from hermes_cli.config import load_config, save_config
import hermes_cli.tools_config as tc
import hermes_cli.tools_config_providers as providers

PLATFORM = "cli"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Each test gets its own HERMES_HOME so disk state is deterministic."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def _seed_plugin_selection(enabled: list[str]) -> None:
    """Write ``plugins.enabled`` the way the plugins subsystem does, then reload."""
    config = load_config()
    config.setdefault("plugins", {})["enabled"] = list(enabled)
    save_config(config)


def _close_toolset_flow(config: dict) -> None:
    """The enclosing writes ``hermes tools`` does after configuring a toolset:
    record the platform selection (``_apply_platform_checklist``) and save the
    object the whole flow has been carrying (``_configure_platforms``)."""
    tc._save_platform_tools(config, PLATFORM, {"vision"})
    save_config(config)


# ---------------------------------------------------------------------------
# Vision: auxiliary.vision survives the enclosing save
# ---------------------------------------------------------------------------

def test_vision_custom_endpoint_survives_outer_save():
    """Full chain: the vision sub-flow writes through the caller's config, the
    enclosing flow saves afterwards, and auxiliary.vision is still on disk
    next to the toolset selection and any plugin config the user already had."""
    _seed_plugin_selection(["web/firecrawl"])
    config_a = load_config()  # the entry flow's object

    seq = iter([2])  # "Custom OpenAI-compatible endpoint"
    prompts = iter(["https://my.endpoint/v1", "sk-secret", "my-vision-model"])
    with patch.object(tc, "_prompt_choice", side_effect=lambda *a, **k: next(seq)), \
         patch.object(providers, "_prompt", side_effect=lambda *a, **k: next(prompts)), \
         patch.object(providers, "save_env_value") as save_env, \
         patch.object(tc, "_toolset_has_keys", return_value=False):
        tc._configure_toolset("vision", config_a, reconfigure=True)

    _close_toolset_flow(config_a)

    fresh = load_config()  # re-read from disk
    vision = fresh.get("auxiliary", {}).get("vision", {})
    assert vision.get("provider") == "custom"
    assert vision.get("base_url") == "https://my.endpoint/v1"
    assert vision.get("model") == "my-vision-model"
    # The toolset the user enabled and the plugin config they already had are both intact.
    assert "vision" in fresh.get("platform_toolsets", {}).get(PLATFORM, [])
    assert "web/firecrawl" in fresh.get("plugins", {}).get("enabled", [])
    save_env.assert_called_once_with("OPENAI_API_KEY", "sk-secret")


def test_vision_auto_survives_outer_save():
    """Auto mode clears pinned keys; the enclosing save must not resurrect
    them from a stale object."""
    config = load_config()
    config.setdefault("auxiliary", {})["vision"] = {
        "provider": "openai", "model": "gpt-4o", "base_url": "x",
    }
    save_config(config)
    config_a = load_config()  # the entry flow's object

    with patch.object(tc, "_prompt_choice", side_effect=lambda *a, **k: 0):  # "Auto"
        tc._configure_toolset("vision", config_a, reconfigure=True)

    _close_toolset_flow(config_a)

    vision = load_config().get("auxiliary", {}).get("vision", {})
    # Pinned overrides cleared; load_config re-fills the auto defaults from
    # DEFAULT_CONFIG, which is exactly the "auto" semantics — none of the
    # previously pinned values may survive.
    assert vision.get("provider") != "openai"
    assert vision.get("model") != "gpt-4o"
    assert vision.get("base_url") != "x"

