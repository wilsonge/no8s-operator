"""
Configuration module for Operator Controller.

Loads configuration from environment variables or config file.
Supports plugin-based architecture with plugin-specific configuration.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DatabaseConfig:
    """PostgreSQL database configuration."""

    host: str = "localhost"
    port: int = 5432
    database: str = "operator_controller"
    user: str = "operator"
    password: str = field(default="", repr=False)  # Never log password
    min_pool_size: int = 5
    max_pool_size: int = 20

    @classmethod
    def from_env(cls):
        """Load from environment variables."""
        password = os.getenv("DB_PASSWORD", "")
        if not password:
            raise ValueError(
                "DB_PASSWORD environment variable must be set. "
                "Database password cannot be empty."
            )

        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "operator_controller"),
            user=os.getenv("DB_USER", "operator"),
            password=password,
            min_pool_size=int(os.getenv("DB_MIN_POOL_SIZE", "5")),
            max_pool_size=int(os.getenv("DB_MAX_POOL_SIZE", "20")),
        )


@dataclass
class ControllerConfig:
    """Controller reconciliation loop configuration."""

    reconcile_interval: int = 60  # seconds
    max_concurrent_reconciles: int = 5

    # Exponential backoff configuration
    backoff_base_delay: int = 60  # base delay in seconds
    backoff_max_delay: int = 3600  # max delay in seconds (1 hour)
    backoff_jitter_factor: float = 0.1  # ±10% jitter

    @classmethod
    def from_env(cls):
        """Load from environment variables."""
        return cls(
            reconcile_interval=int(os.getenv("RECONCILE_INTERVAL", "60")),
            max_concurrent_reconciles=int(os.getenv("MAX_CONCURRENT_RECONCILES", "5")),
            backoff_base_delay=int(os.getenv("BACKOFF_BASE_DELAY", "60")),
            backoff_max_delay=int(os.getenv("BACKOFF_MAX_DELAY", "3600")),
            backoff_jitter_factor=float(os.getenv("BACKOFF_JITTER_FACTOR", "0.1")),
        )


@dataclass
class APIConfig:
    """API server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_enabled: bool = False
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls):
        """Load from environment variables."""
        cors_origins = (
            os.getenv("CORS_ORIGINS", "").split(",")
            if os.getenv("CORS_ORIGINS")
            else ["*"]
        )
        return cls(
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            cors_enabled=os.getenv("CORS_ENABLED", "false").lower() == "true",
            cors_origins=cors_origins,
        )


@dataclass
class PluginConfig:
    """Plugin system configuration."""

    # List of enabled plugin names (empty = use all registered plugins)
    enabled_action_plugins: List[str] = field(default_factory=list)
    enabled_input_plugins: List[str] = field(default_factory=list)

    # Plugin-specific configurations keyed by plugin name
    plugin_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_env(cls):
        """Load from environment variables."""
        enabled_actions_str = os.getenv("ENABLED_ACTION_PLUGINS", "")
        enabled_inputs_str = os.getenv("ENABLED_INPUT_PLUGINS", "")

        enabled_actions = (
            [p.strip() for p in enabled_actions_str.split(",") if p.strip()]
            if enabled_actions_str
            else []
        )
        enabled_inputs = (
            [p.strip() for p in enabled_inputs_str.split(",") if p.strip()]
            if enabled_inputs_str
            else []
        )

        # Load plugin configs from JSON environment variable
        plugin_configs = {}
        if os.getenv("PLUGIN_CONFIGS"):
            try:
                plugin_configs = json.loads(os.getenv("PLUGIN_CONFIGS"))
            except json.JSONDecodeError:
                pass

        return cls(
            enabled_action_plugins=enabled_actions,
            enabled_input_plugins=enabled_inputs,
            plugin_configs=plugin_configs,
        )

    def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """Get configuration for a specific plugin."""
        return self.plugin_configs.get(plugin_name, {})


@dataclass
class Config:
    """Main configuration object."""

    database: DatabaseConfig
    controller: ControllerConfig
    api: APIConfig
    plugins: PluginConfig

    @classmethod
    def from_env(cls):
        """Load all configuration from environment variables."""
        return cls(
            database=DatabaseConfig.from_env(),
            controller=ControllerConfig.from_env(),
            api=APIConfig.from_env(),
            plugins=PluginConfig.from_env(),
        )

    @classmethod
    def default(cls):
        """Return default configuration."""
        return cls(
            database=DatabaseConfig(),
            controller=ControllerConfig(),
            api=APIConfig(),
            plugins=PluginConfig(),
        )


# Global config instance
config: Optional[Config] = None


def load_config() -> Config:
    """Load configuration (singleton pattern)."""
    global config
    if config is None:
        config = Config.from_env()
    return config


def get_config() -> Config:
    """Get the current configuration."""
    if config is None:
        return load_config()
    return config


def reset_config() -> None:
    """Reset configuration (mainly for testing)."""
    global config
    config = None
