"""Unit tests for config.py - Configuration management."""

import os
import pytest
from unittest.mock import patch

import config
from config import (
    DatabaseConfig,
    ControllerConfig,
    APIConfig,
    PluginConfig,
    Config,
    load_config,
    get_config,
    reset_config,
)


class TestDatabaseConfig:
    """Tests for DatabaseConfig class."""

    def test_default_values(self):
        """Test default configuration values."""
        cfg = DatabaseConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 5432
        assert cfg.database == "operator_controller"
        assert cfg.user == "operator"
        assert cfg.password == ""
        assert cfg.min_pool_size == 5
        assert cfg.max_pool_size == 20

    def test_custom_values(self):
        """Test custom configuration values."""
        cfg = DatabaseConfig(
            host="db.example.com",
            port=5433,
            database="mydb",
            user="myuser",
            password="secret",
            min_pool_size=10,
            max_pool_size=50,
        )
        assert cfg.host == "db.example.com"
        assert cfg.port == 5433
        assert cfg.database == "mydb"
        assert cfg.user == "myuser"
        assert cfg.password == "secret"
        assert cfg.min_pool_size == 10
        assert cfg.max_pool_size == 50

    def test_from_env(self):
        """Test loading configuration from environment variables."""
        env_vars = {
            "DB_HOST": "envhost",
            "DB_PORT": "5434",
            "DB_NAME": "envdb",
            "DB_USER": "envuser",
            "DB_PASSWORD": "envpassword",
            "DB_MIN_POOL_SIZE": "3",
            "DB_MAX_POOL_SIZE": "15",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = DatabaseConfig.from_env()
            assert cfg.host == "envhost"
            assert cfg.port == 5434
            assert cfg.database == "envdb"
            assert cfg.user == "envuser"
            assert cfg.password == "envpassword"
            assert cfg.min_pool_size == 3
            assert cfg.max_pool_size == 15

    def test_from_env_missing_password_raises(self):
        """Test that missing password raises ValueError."""
        env_vars = {
            "DB_HOST": "localhost",
            "DB_PASSWORD": "",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(ValueError) as exc_info:
                DatabaseConfig.from_env()
            assert "DB_PASSWORD" in str(exc_info.value)

    def test_password_not_in_repr(self):
        """Test that password is not exposed in repr."""
        cfg = DatabaseConfig(password="secret123")
        repr_str = repr(cfg)
        assert "secret123" not in repr_str


class TestControllerConfig:
    """Tests for ControllerConfig class."""

    def test_default_values(self):
        """Test default configuration values."""
        cfg = ControllerConfig()
        assert cfg.reconcile_interval == 60
        assert cfg.max_concurrent_reconciles == 5
        assert cfg.backoff_base_delay == 60
        assert cfg.backoff_max_delay == 3600
        assert cfg.backoff_jitter_factor == 0.1

    def test_custom_values(self):
        """Test custom configuration values."""
        cfg = ControllerConfig(
            reconcile_interval=30,
            max_concurrent_reconciles=10,
            backoff_base_delay=120,
            backoff_max_delay=7200,
            backoff_jitter_factor=0.2,
        )
        assert cfg.reconcile_interval == 30
        assert cfg.max_concurrent_reconciles == 10
        assert cfg.backoff_base_delay == 120
        assert cfg.backoff_max_delay == 7200
        assert cfg.backoff_jitter_factor == 0.2

    def test_from_env(self):
        """Test loading configuration from environment variables."""
        env_vars = {
            "RECONCILE_INTERVAL": "45",
            "MAX_CONCURRENT_RECONCILES": "8",
            "BACKOFF_BASE_DELAY": "90",
            "BACKOFF_MAX_DELAY": "5400",
            "BACKOFF_JITTER_FACTOR": "0.15",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = ControllerConfig.from_env()
            assert cfg.reconcile_interval == 45
            assert cfg.max_concurrent_reconciles == 8
            assert cfg.backoff_base_delay == 90
            assert cfg.backoff_max_delay == 5400
            assert cfg.backoff_jitter_factor == 0.15

    def test_from_env_defaults(self):
        """Test that defaults are used when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = ControllerConfig.from_env()
            assert cfg.reconcile_interval == 60
            assert cfg.max_concurrent_reconciles == 5


class TestAPIConfig:
    """Tests for APIConfig class."""

    def test_default_values(self):
        """Test default configuration values."""
        cfg = APIConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8000
        assert cfg.log_level == "INFO"
        assert cfg.cors_enabled is False
        assert cfg.cors_origins == ["*"]

    def test_custom_values(self):
        """Test custom configuration values."""
        cfg = APIConfig(
            host="127.0.0.1",
            port=9000,
            log_level="DEBUG",
            cors_enabled=True,
            cors_origins=["http://localhost:3000"],
        )
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9000
        assert cfg.log_level == "DEBUG"
        assert cfg.cors_enabled is True
        assert cfg.cors_origins == ["http://localhost:3000"]

    def test_from_env(self):
        """Test loading configuration from environment variables."""
        env_vars = {
            "API_HOST": "0.0.0.0",
            "API_PORT": "3000",
            "LOG_LEVEL": "DEBUG",
            "CORS_ENABLED": "true",
            "CORS_ORIGINS": "http://localhost:3000,https://example.com",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = APIConfig.from_env()
            assert cfg.host == "0.0.0.0"
            assert cfg.port == 3000
            assert cfg.log_level == "DEBUG"
            assert cfg.cors_enabled is True
            assert cfg.cors_origins == ["http://localhost:3000", "https://example.com"]

    def test_from_env_cors_disabled(self):
        """Test CORS disabled configuration."""
        env_vars = {
            "CORS_ENABLED": "false",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = APIConfig.from_env()
            assert cfg.cors_enabled is False

    def test_from_env_no_cors_origins(self):
        """Test default CORS origins when not specified."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = APIConfig.from_env()
            assert cfg.cors_origins == ["*"]


class TestPluginConfig:
    """Tests for PluginConfig class."""

    def test_default_values(self):
        """Test default configuration values."""
        cfg = PluginConfig()
        assert cfg.enabled_action_plugins == []
        assert cfg.enabled_input_plugins == []
        assert cfg.plugin_configs == {}

    def test_custom_values(self):
        """Test custom configuration values."""
        cfg = PluginConfig(
            enabled_action_plugins=["github_actions"],
            enabled_input_plugins=["http"],
            plugin_configs={"github_actions": {"timeout": 3600}},
        )
        assert cfg.enabled_action_plugins == ["github_actions"]
        assert cfg.enabled_input_plugins == ["http"]
        assert cfg.plugin_configs == {"github_actions": {"timeout": 3600}}

    def test_from_env(self):
        """Test loading configuration from environment variables."""
        env_vars = {
            "ENABLED_ACTION_PLUGINS": "github_actions,terraform",
            "ENABLED_INPUT_PLUGINS": "http,sqs",
            "PLUGIN_CONFIGS": '{"github_actions": {"timeout": 1800}}',
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = PluginConfig.from_env()
            assert cfg.enabled_action_plugins == ["github_actions", "terraform"]
            assert cfg.enabled_input_plugins == ["http", "sqs"]
            assert cfg.plugin_configs == {"github_actions": {"timeout": 1800}}

    def test_from_env_empty_plugins(self):
        """Test empty plugin lists."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = PluginConfig.from_env()
            assert cfg.enabled_action_plugins == []
            assert cfg.enabled_input_plugins == []

    def test_from_env_invalid_json(self):
        """Test that invalid JSON in PLUGIN_CONFIGS is handled gracefully."""
        env_vars = {
            "PLUGIN_CONFIGS": "not valid json",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = PluginConfig.from_env()
            assert cfg.plugin_configs == {}

    def test_get_plugin_config(self):
        """Test get_plugin_config method."""
        cfg = PluginConfig(
            plugin_configs={
                "github_actions": {"timeout": 3600},
                "terraform": {"version": "1.5.0"},
            }
        )
        assert cfg.get_plugin_config("github_actions") == {"timeout": 3600}
        assert cfg.get_plugin_config("terraform") == {"version": "1.5.0"}
        assert cfg.get_plugin_config("nonexistent") == {}

    def test_from_env_whitespace_handling(self):
        """Test that whitespace in plugin lists is handled."""
        env_vars = {
            "ENABLED_ACTION_PLUGINS": " github_actions , terraform ",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = PluginConfig.from_env()
            assert cfg.enabled_action_plugins == ["github_actions", "terraform"]


class TestConfig:
    """Tests for main Config class."""

    def test_default(self):
        """Test default configuration."""
        cfg = Config.default()
        assert isinstance(cfg.database, DatabaseConfig)
        assert isinstance(cfg.controller, ControllerConfig)
        assert isinstance(cfg.api, APIConfig)
        assert isinstance(cfg.plugins, PluginConfig)

    def test_from_env(self):
        """Test loading full configuration from environment."""
        env_vars = {
            "DB_HOST": "testhost",
            "DB_PASSWORD": "testpass",
            "RECONCILE_INTERVAL": "30",
            "API_PORT": "9000",
            "ENABLED_ACTION_PLUGINS": "github_actions",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = Config.from_env()
            assert cfg.database.host == "testhost"
            assert cfg.database.password == "testpass"
            assert cfg.controller.reconcile_interval == 30
            assert cfg.api.port == 9000
            assert cfg.plugins.enabled_action_plugins == ["github_actions"]


class TestConfigSingleton:
    """Tests for config singleton functions."""

    def setup_method(self):
        """Reset config before each test."""
        reset_config()

    def teardown_method(self):
        """Reset config after each test."""
        reset_config()

    def test_load_config(self):
        """Test load_config function."""
        env_vars = {
            "DB_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = load_config()
            assert cfg is not None
            assert isinstance(cfg, Config)

    def test_get_config_loads_if_none(self):
        """Test get_config loads config if not loaded."""
        env_vars = {
            "DB_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg = get_config()
            assert cfg is not None
            assert isinstance(cfg, Config)

    def test_singleton_returns_same_instance(self):
        """Test that singleton returns same instance."""
        env_vars = {
            "DB_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg1 = load_config()
            cfg2 = get_config()
            assert cfg1 is cfg2

    def test_reset_config(self):
        """Test reset_config clears the singleton."""
        env_vars = {
            "DB_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            cfg1 = load_config()
            reset_config()
            assert config.config is None
            cfg2 = load_config()
            # After reset, should be a new instance
            assert cfg1 is not cfg2