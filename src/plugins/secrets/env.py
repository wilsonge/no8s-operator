"""
Environment Variable Secret Store - default built-in backend.

Resolves secrets from process environment variables.  This is the
default backend and requires no external dependencies.
"""

import os
from typing import Any, Dict

from plugins.secrets.base import SecretStorePlugin


class EnvSecretStore(SecretStorePlugin):
    """Secret store that reads values from environment variables."""

    @property
    def name(self) -> str:
        return "env"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        pass  # No initialisation required for env-var backend.

    async def get_secret(self, key: str) -> str:
        """
        Retrieve a secret from the environment.

        Args:
            key: The environment variable name.

        Returns:
            The environment variable value.

        Raises:
            KeyError: If the environment variable is not set or is empty.
        """
        value = os.getenv(key, "")
        if not value:
            raise KeyError(f"Secret '{key}' not found in environment variables.")
        return value
