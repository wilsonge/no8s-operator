"""
Plugin Registry - Discovery and registration of plugins.

This module provides the central registry for all plugins, handling
discovery, registration, and instantiation.
"""

from importlib.metadata import entry_points
from typing import Any, Dict, Optional, Type

from plugins.base import logger
from plugins.actions.base import ActionPlugin
from plugins.inputs.base import InputPlugin


class PluginRegistry:
    """
    Central registry for all plugins.

    Handles discovery, registration, and instantiation of action, input,
    and reconciler plugins.
    """

    def __init__(self):
        # Registered plugin classes (not instantiated)
        self._action_plugins: Dict[str, Type[ActionPlugin]] = {}
        self._input_plugins: Dict[str, Type[InputPlugin]] = {}
        self._reconciler_plugins: Dict[str, Type] = {}

        # Cached plugin metadata (name, version) to avoid repeated instantiation
        self._action_plugin_info: Dict[str, Dict[str, str]] = {}
        self._input_plugin_info: Dict[str, Dict[str, str]] = {}
        self._reconciler_plugin_info: Dict[str, Dict[str, Any]] = {}

        # Instantiated and initialized plugin instances
        self._action_instances: Dict[str, ActionPlugin] = {}
        self._input_instances: Dict[str, InputPlugin] = {}
        self._reconciler_instances: Dict[str, Any] = {}

        # Plugin configurations loaded from environment
        self._action_plugin_configs: Dict[str, Dict[str, Any]] = {}
        self._input_plugin_configs: Dict[str, Dict[str, Any]] = {}

        # Mapping from resource type name to reconciler plugin name
        self._resource_type_to_reconciler: Dict[str, str] = {}

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

    def register_reconciler_plugin(self, plugin_class: Type) -> None:
        """
        Register a reconciler plugin class.

        Args:
            plugin_class: The ReconcilerPlugin subclass to register

        Raises:
            ValueError: If a resource type is already claimed by another reconciler
        """
        temp_instance = plugin_class()
        name = temp_instance.name
        resource_types = temp_instance.resource_types

        if name in self._reconciler_plugins:
            logger.warning(f"Overwriting existing reconciler plugin: {name}")

        # Check for resource type conflicts
        for rt in resource_types:
            existing = self._resource_type_to_reconciler.get(rt)
            if existing and existing != name:
                raise ValueError(
                    f"Resource type '{rt}' is already claimed by "
                    f"reconciler '{existing}'. Cannot register '{name}'."
                )

        self._reconciler_plugins[name] = plugin_class
        self._reconciler_plugin_info[name] = {
            "name": name,
            "resource_types": resource_types,
        }

        for rt in resource_types:
            self._resource_type_to_reconciler[rt] = name

        logger.info(
            f"Registered reconciler plugin: {name} "
            f"(resource types: {', '.join(resource_types)})"
        )

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

    def get_reconciler_plugin(self, name: str) -> Any:
        """
        Get a reconciler plugin instance (not async — no initialize step).

        Args:
            name: The reconciler plugin name

        Returns:
            A ReconcilerPlugin instance

        Raises:
            ValueError: If the reconciler name is not registered
        """
        if name not in self._reconciler_plugins:
            available = ", ".join(self._reconciler_plugins.keys()) or "none"
            raise ValueError(
                f"Unknown reconciler plugin: {name}. "
                f"Available reconcilers: {available}"
            )

        if name not in self._reconciler_instances:
            self._reconciler_instances[name] = self._reconciler_plugins[name]()
            logger.info(f"Instantiated reconciler plugin: {name}")

        return self._reconciler_instances[name]

    # Discovery methods

    def list_action_plugins(self) -> list[str]:
        """List all registered action plugin names."""
        return list(self._action_plugins.keys())

    def list_input_plugins(self) -> list[str]:
        """List all registered input plugin names."""
        return list(self._input_plugins.keys())

    def list_reconciler_plugins(self) -> list[str]:
        """List all registered reconciler plugin names."""
        return list(self._reconciler_plugins.keys())

    def has_action_plugin(self, name: str) -> bool:
        """Check if an action plugin is registered."""
        return name in self._action_plugins

    def has_input_plugin(self, name: str) -> bool:
        """Check if an input plugin is registered."""
        return name in self._input_plugins

    def has_reconciler_for_resource_type(self, resource_type_name: str) -> bool:
        """Check if any reconciler handles the given resource type."""
        return resource_type_name in self._resource_type_to_reconciler

    def get_reconciler_for_resource_type(
        self, resource_type_name: str
    ) -> Optional[Any]:
        """
        Get the reconciler instance for a resource type.

        Args:
            resource_type_name: The resource type name

        Returns:
            A ReconcilerPlugin instance, or None if no reconciler handles it
        """
        reconciler_name = self._resource_type_to_reconciler.get(resource_type_name)
        if reconciler_name is None:
            return None
        return self.get_reconciler_plugin(reconciler_name)

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

    def get_reconciler_plugin_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a registered reconciler plugin.

        Args:
            name: The reconciler plugin name

        Returns:
            Dictionary with 'name' and 'resource_types', or None if not found
        """
        return self._reconciler_plugin_info.get(name)

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
    Register all built-in plugins and discover reconciler plugins
    via entry points.

    This function is called during application startup to register
    the default plugins that ship with the controller and to discover
    any installed reconciler plugins.
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

    # Discover and register reconciler plugins via entry points
    discovered = entry_points(group="no8s.reconcilers")
    for ep in discovered:
        try:
            reconciler_class = ep.load()
            registry.register_reconciler_plugin(reconciler_class)
        except Exception as e:
            logger.warning(f"Could not load reconciler plugin {ep.name}: {e}")
