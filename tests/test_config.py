"""Smoke tests for configuration loading."""

import pytest

from sqa_agent.config import (
    AgentConfig,
    Config,
    ConfigMigrationError,
    ResolveConfig,
    RunToolsConfig,
    load_config,
    _validate_model,
)


def test_agent_config_defaults():
    config = AgentConfig()
    assert config.review_model == "claude-opus-4-7"
    assert config.resolve_model == "claude-opus-4-7"
    assert config.thinking == "adaptive"
    assert config.effort == "xhigh"


def test_config_defaults():
    config = Config()
    assert config.include == []
    assert config.exclude == []
    assert config.tools == {}
    assert isinstance(config.agent, AgentConfig)
    assert isinstance(config.resolve, ResolveConfig)
    assert isinstance(config.menu, RunToolsConfig)


class TestModelValidation:
    """Tests for model name validation."""

    def test_valid_models(self):
        for name in [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-5",
            "claude-opus-4-1-20250805",
        ]:
            _validate_model(name, "test_field")  # should not raise

    def test_typo_double_t(self):
        with pytest.raises(
            ValueError, match="doesn't look like a valid Claude model ID"
        ):
            _validate_model("claude-sonnett-4-6", "review_model")

    def test_missing_prefix(self):
        with pytest.raises(
            ValueError, match="doesn't look like a valid Claude model ID"
        ):
            _validate_model("sonnet-4-6", "review_model")

    def test_garbage(self):
        with pytest.raises(
            ValueError, match="doesn't look like a valid Claude model ID"
        ):
            _validate_model("gpt-4o", "review_model")

    def test_agent_config_rejects_bad_review_model(self):
        with pytest.raises(ValueError, match="review_model"):
            AgentConfig(review_model="claude-sonnett-4-6")

    def test_agent_config_rejects_bad_resolve_model(self):
        with pytest.raises(ValueError, match="resolve_model"):
            AgentConfig(resolve_model="claude-opuss-4-6")


class TestRunToolsConfig:
    """Tests for RunToolsConfig defaults and construction."""

    def test_defaults_all_false(self):
        rtc = RunToolsConfig()
        assert rtc.formatter is False
        assert rtc.linter is False
        assert rtc.type_checker is False
        assert rtc.test is False

    def test_partial_override(self):
        rtc = RunToolsConfig(linter=True, test=True)
        assert rtc.formatter is False
        assert rtc.linter is True
        assert rtc.type_checker is False
        assert rtc.test is True


class TestResolveConfig:
    """Tests for ResolveConfig defaults."""

    def test_defaults(self):
        rc = ResolveConfig()
        assert isinstance(rc.auto, RunToolsConfig)
        assert isinstance(rc.interactive, RunToolsConfig)
        assert rc.auto.formatter is False
        assert rc.interactive.formatter is False


class TestLoadConfigRunTools:
    """Tests for loading [resolve] and [menu] sections from TOML."""

    def test_old_config_backward_compatible(self, tmp_path):
        """A config with no [resolve] or [menu] sections loads with all-false defaults."""
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
        )
        config = load_config(p)
        assert config.resolve.auto.formatter is False
        assert config.resolve.auto.linter is False
        assert config.resolve.interactive.formatter is False
        assert config.menu.formatter is False

    def test_resolve_auto_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[resolve.auto]\nformatter = true\nlinter = true\n"
        )
        config = load_config(p)
        assert config.resolve.auto.formatter is True
        assert config.resolve.auto.linter is True
        assert config.resolve.auto.type_checker is False
        assert config.resolve.auto.test is False
        # interactive untouched
        assert config.resolve.interactive.formatter is False

    def test_resolve_interactive_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[resolve.interactive]\ntype_checker = true\ntest = true\n"
        )
        config = load_config(p)
        assert config.resolve.interactive.type_checker is True
        assert config.resolve.interactive.test is True
        assert config.resolve.auto.type_checker is False

    def test_menu_section(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[menu]\nformatter = true\nlinter = true\n"
            "type_checker = true\ntest = true\n"
        )
        config = load_config(p)
        assert config.menu.formatter is True
        assert config.menu.linter is True
        assert config.menu.type_checker is True
        assert config.menu.test is True

    def test_resolve_unknown_key_rejected(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[resolve]\nbogus = true\n"
        )
        with pytest.raises(ValueError, match="resolve: unknown keys"):
            load_config(p)

    def test_resolve_auto_unknown_key_rejected(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[resolve.auto]\nbogus = true\n"
        )
        with pytest.raises(ValueError, match="resolve.auto: unknown keys"):
            load_config(p)

    def test_menu_unknown_key_rejected(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            "[menu]\nbogus = true\n"
        )
        with pytest.raises(ValueError, match="menu: unknown keys"):
            load_config(p)

    def test_non_boolean_rejected(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            '[agent]\nreview_model = "claude-sonnet-4-6"\n'
            'resolve_model = "claude-opus-4-6"\n'
            '[menu]\nformatter = "yes"\n'
        )
        with pytest.raises(ValueError, match="must be a boolean"):
            load_config(p)


class TestAgentConfigNewFields:
    """Tests for the thinking / effort fields."""

    def test_thinking_disabled_accepted(self):
        cfg = AgentConfig(thinking="disabled")
        assert cfg.thinking == "disabled"

    def test_thinking_invalid_rejected(self):
        with pytest.raises(ValueError, match="thinking='enabled' is not valid"):
            AgentConfig(thinking="enabled")  # type: ignore[arg-type]

    def test_effort_all_levels_accepted(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            cfg = AgentConfig(effort=level)  # type: ignore[arg-type]
            assert cfg.effort == level

    def test_effort_invalid_rejected(self):
        with pytest.raises(ValueError, match="effort='ultra' is not valid"):
            AgentConfig(effort="ultra")  # type: ignore[arg-type]

    def test_load_config_with_new_fields(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            "[agent]\n"
            'review_model  = "claude-opus-4-7"\n'
            'resolve_model = "claude-opus-4-7"\n'
            'thinking      = "disabled"\n'
            'effort        = "high"\n'
        )
        config = load_config(p)
        assert config.agent.thinking == "disabled"
        assert config.agent.effort == "high"


class TestMaxThinkingTokensDeprecation:
    """Tests for the `max_thinking_tokens` migration error."""

    def test_migration_error_on_deprecated_field(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text(
            "[agent]\n"
            'review_model = "claude-opus-4-7"\n'
            'resolve_model = "claude-opus-4-7"\n'
            "max_thinking_tokens = 10000\n"
        )
        with pytest.raises(ConfigMigrationError) as exc_info:
            load_config(p)
        msg = exc_info.value.user_message
        assert "max_thinking_tokens" in msg
        assert "adaptive" in msg
        assert "effort" in msg

    def test_migration_error_is_value_error(self, tmp_path):
        """ConfigMigrationError must subclass ValueError for back-compat."""
        p = tmp_path / "config.toml"
        p.write_text("[agent]\nmax_thinking_tokens = 5000\n")
        with pytest.raises(ValueError):
            load_config(p)
