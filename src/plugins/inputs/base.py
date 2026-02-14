"""
Input Plugin Base - Abstract interface for resource input sources.

Input plugins provide mechanisms for users to submit resources:
- HTTP API: REST endpoints
- GitOps: Watch Git repositories
- File watcher: Watch local files
- Queue listener: Listen to message queues (SQS, RabbitMQ, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from plugins.base import ResourceSpec

# Callback type for when resources are created/updated/deleted
# (event_type: str, spec: ResourceSpec) -> None
ResourceCallback = Callable[[str, ResourceSpec], Awaitable[None]]


@dataclass
class ValidationResult:
    """Result of a validation operation."""

    is_valid: bool
    error_message: Optional[str] = None


def validate_action_plugin(action_plugin: str) -> ValidationResult:
    """
    Validate that an action plugin is registered and available.

    This helper function can be used by any input plugin to validate
    that a requested action plugin exists before creating a resource.

    Args:
        action_plugin: The name of the action plugin to validate

    Returns:
        ValidationResult with is_valid=True if plugin exists,
        or is_valid=False with an error message if not found
    """
    from plugins.registry import get_registry

    registry = get_registry()
    if not registry.has_action_plugin(action_plugin):
        available = registry.list_action_plugins()
        return ValidationResult(
            is_valid=False,
            error_message=f"Unknown action plugin: {action_plugin}. "
            f"Available plugins: {', '.join(available) or 'none'}",
        )
    return ValidationResult(is_valid=True)


class InputPlugin(ABC):
    """
    Abstract base class for input plugins.

    Input plugins are responsible for receiving resource specifications
    from external sources and notifying the controller of changes.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin (e.g., 'http', 'gitops')."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version string."""
        pass

    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialize the plugin with configuration.

        Called once when the plugin is loaded. Use this to set up
        connections, validate configuration, etc.

        Args:
            config: Plugin-specific configuration dictionary
        """
        pass

    @abstractmethod
    async def start(self, on_resource_event: ResourceCallback) -> None:
        """
        Start the input plugin.

        This method should start listening for resource events and
        call the callback when resources are created/updated/deleted.

        For HTTP plugins, this starts the HTTP server.
        For GitOps plugins, this starts watching repositories.
        For queue plugins, this starts consuming messages.

        Args:
            on_resource_event: Callback to invoke when resource events occur.
                              First arg is event type ('created', 'updated', 'deleted'),
                              second arg is the ResourceSpec.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the input plugin gracefully.

        This should cleanly shut down any servers, watchers, or connections.
        """
        pass

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """
        Check if the input plugin is healthy.

        Returns:
            Tuple of (is_healthy, status_message).
        """
        pass

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        """
        Load plugin-specific configuration from environment variables.

        Override this method in subclasses to define how the plugin
        loads its configuration from the environment.

        Returns:
            Dictionary of configuration values for this plugin.
        """
        return {}

    def set_db_manager(self, db_manager: Any) -> None:
        """
        Set the database manager for plugins that need database access.

        Override this method in subclasses that require database access.

        Args:
            db_manager: The DatabaseManager instance
        """
        pass

    def set_event_bus(self, event_bus: Any) -> None:
        """
        Set the event bus for plugins that publish or stream events.

        Override this method in subclasses that require event bus access.

        Args:
            event_bus: The EventBus instance
        """
        pass
