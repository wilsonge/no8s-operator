"""
Plugin Registry - Discovery and registration of plugins.

This module provides the central registry for all plugins, handling
discovery, registration, and instantiation.
"""

from typing import Any, Dict, Optional, Type

from plugins.base import logger
from plugins.actions.base import ActionPlugin
from plugins.inputs.base import InputPlugin


class PluginRegistry:
    """
    Central registry for all plugins.

    Handles discovery, registration, and instantiation of action and input plugins.
    """

    def __init__(self):
        # Registered plugin classes (not instantiated)
        self._action_plugins: Dict[str, Type[ActionPlugin]] = {}
        self._input_plugins: Dict[str, Type[InputPlugin]] = {}

        # Cached plugin metadata (name, version) to avoid repeated instantiation
        self._action_plugin_info: Dict[str, Dict[str, str]] = {}
        self._input_plugin_info: Dict[str, Dict[str, str]] = {}

        # Instantiated and initialized plugin instances
        self._action_instances: Dict[str, ActionPlugin] = {}
        self._input_instances: Dict[str, InputPlugin] = {}

        # Plugin configurations loaded from environment
        self._action_plugin_configs: Dict[str, Dict[str, Any]] = {}
        self._input_plugin_configs: Dict[str, Dict[str, Any]] = {}

    # Registration methods

    def register_action_plugin(self, plugin_class: Type[ActionPlugin]) -> None:
        """
        Register an action plugin class.

        Args:
            plugin_class: The ActionPlugin subclass to register
        """
        # Create temporary instance to get name/version (only once at registration)
        temp_instance = plugin_class()
        name = temp_instance.name
        version = temp_instance.version

        if name in self._action_plugins:
            logger.warning(f"Overwriting existing action plugin: {name}")

        self._action_plugins[name] = plugin_class
        # Cache metadata to avoid repeated instantiation
        self._action_plugin_info[name] = {"name": name, "version": version}
        # Load plugin config from environment
        self._action_plugin_configs[name] = plugin_class.load_config_from_env()
        logger.info(f"Registered action plugin: {name} v{version}")

    def register_input_plugin(self, plugin_class: Type[InputPlugin]) -> None:
        """
        Register an input plugin class.

        Args:
            plugin_class: The InputPlugin subclass to register
        """
        # Create temporary instance to get name/version (only once at registration)
        temp_instance = plugin_class()
        name = temp_instance.name
        version = temp_instance.version

        if name in self._input_plugins:
            logger.warning(f"Overwriting existing input plugin: {name}")

        self._input_plugins[name] = plugin_class
        # Cache metadata to avoid repeated instantiation
        self._input_plugin_info[name] = {"name": name, "version": version}
        # Load plugin config from environment
        self._input_plugin_configs[name] = plugin_class.load_config_from_env()
        logger.info(f"Registered input plugin: {name} v{version}")

    # Instantiation methods

    async def get_action_plugin(
        self, name: str, config: Optional[Dict[str, Any]] = None
    ) -> ActionPlugin:
        """
        Get an initialized action plugin instance.

        Args:
            name: The plugin name to retrieve
            config: Optional configuration to pass to initialize()

        Returns:
            An initialized ActionPlugin instance

        Raises:
            ValueError: If the plugin name is not registered
        """
        if name not in self._action_plugins:
            available = ", ".join(self._action_plugins.keys()) or "none"
            raise ValueError(
                f"Unknown action plugin: {name}. Available plugins: {available}"
            )

        if name not in self._action_instances:
            plugin = self._action_plugins[name]()
            await plugin.initialize(config or {})
            self._action_instances[name] = plugin
            logger.info(f"Initialized action plugin: {name}")

        return self._action_instances[name]

    async def get_input_plugin(
        self, name: str, config: Optional[Dict[str, Any]] = None
    ) -> InputPlugin:
        """
        Get an initialized input plugin instance.

        Args:
            name: The plugin name to retrieve
            config: Optional configuration to pass to initialize()

        Returns:
            An initialized InputPlugin instance

        Raises:
            ValueError: If the plugin name is not registered
        """
        if name not in self._input_plugins:
            available = ", ".join(self._input_plugins.keys()) or "none"
            raise ValueError(
                f"Unknown input plugin: {name}. Available plugins: {available}"
            )

        if name not in self._input_instances:
            plugin = self._input_plugins[name]()
            await plugin.initialize(config or {})
            self._input_instances[name] = plugin
            logger.info(f"Initialized input plugin: {name}")

        return self._input_instances[name]

    # Discovery methods

    def list_action_plugins(self) -> list[str]:
        """List all registered action plugin names."""
        return list(self._action_plugins.keys())

    def list_input_plugins(self) -> list[str]:
        """List all registered input plugin names."""
        return list(self._input_plugins.keys())

    def has_action_plugin(self, name: str) -> bool:
        """Check if an action plugin is registered."""
        return name in self._action_plugins

    def has_input_plugin(self, name: str) -> bool:
        """Check if an input plugin is registered."""
        return name in self._input_plugins

    def get_action_plugin_info(self, name: str) -> Optional[Dict[str, str]]:
        """
        Get information about a registered action plugin.

        Args:
            name: The plugin name

        Returns:
            Dictionary with 'name' and 'version', or None if not found
        """
        return self._action_plugin_info.get(name)

    def get_input_plugin_info(self, name: str) -> Optional[Dict[str, str]]:
        """
        Get information about a registered input plugin.

        Args:
            name: The plugin name

        Returns:
            Dictionary with 'name' and 'version', or None if not found
        """
        return self._input_plugin_info.get(name)

    def get_action_plugin_config(self, name: str) -> Dict[str, Any]:
        """
        Get configuration for an action plugin.

        Args:
            name: The plugin name

        Returns:
            Dictionary of configuration values, or empty dict if not found
        """
        return self._action_plugin_configs.get(name, {})

    def get_input_plugin_config(self, name: str) -> Dict[str, Any]:
        """
        Get configuration for an input plugin.

        Args:
            name: The plugin name

        Returns:
            Dictionary of configuration values, or empty dict if not found
        """
        return self._input_plugin_configs.get(name, {})


# Global registry instance
_registry: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Get the global plugin registry singleton."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (mainly for testing)."""
    global _registry
    _registry = None


def register_builtin_plugins() -> None:
    """
    Register all built-in plugins.

    This function is called during application startup to register
    the default plugins that ship with the controller.
    """
    registry = get_registry()

    # Import and register built-in action plugins
    try:
        from plugins.actions.github_actions import GitHubActionsPlugin

        registry.register_action_plugin(GitHubActionsPlugin)
    except ImportError as e:
        logger.warning(f"Could not load GitHub Actions plugin: {e}")

    # Import and register built-in input plugins
    try:
        from plugins.inputs.http import HTTPInputPlugin

        registry.register_input_plugin(HTTPInputPlugin)
    except ImportError as e:
        logger.warning(f"Could not load HTTP input plugin: {e}")
