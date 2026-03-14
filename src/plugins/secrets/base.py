"""
Secret Store Plugin Base - Abstract interface for secret backends.

Secret store plugins allow the operator to retrieve secrets from different
backends (environment variables, HashiCorp Vault, AWS Secrets Manager, etc.).
They are discovered via the 'no8s.secret_stores' entry point group.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class SecretStorePlugin(ABC):
    """
    Abstract base class for secret store plugins.

    A secret store plugin retrieves secret values by key.  The default
    built-in backend reads values from environment variables.  Third-party
    packages can provide alternative backends (e.g. Vault, AWS Secrets
    Manager) by implementing this interface and registering an entry point
    in the 'no8s.secret_stores' group.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin (e.g., 'env', 'vault')."""
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

        Called once when the plugin is loaded.  Use this to establish
        connections, load credentials, or validate configuration.

        Args:
            config: Plugin-specific configuration dictionary.
        """
        pass

    @abstractmethod
    async def get_secret(self, key: str) -> str:
        """
        Retrieve a secret value by key.

        Args:
            key: The secret key / path to look up.

        Returns:
            The secret value as a string.

        Raises:
            KeyError: If the secret is not found.
        """
        pass

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        """
        Load plugin-specific configuration from environment variables.

        Override this in subclasses to define environment-based
        configuration.  The returned dictionary is passed to initialize().

        Returns:
            Dictionary of configuration values for this plugin.
        """
        return {}
