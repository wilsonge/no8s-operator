"""
HashiCorp Vault Secret Store backend.

Uses the Vault HTTP API directly via aiohttp (no extra dependencies).

Configuration environment variables:

    VAULT_ADDR        - Vault server URL (default: http://127.0.0.1:8200)
    VAULT_TOKEN       - Token for authentication
    VAULT_NAMESPACE   - Vault Enterprise namespace (optional)
    VAULT_MOUNT       - KV v2 secrets mount path (default: secret)
"""

import os
from typing import Any, Dict, Optional

import aiohttp

from plugins.secrets.base import SecretStorePlugin


class VaultSecretStore(SecretStorePlugin):
    """
    Secret store plugin backed by HashiCorp Vault KV v2.

    Secrets are expected to be stored under <mount>/data/<key> and the
    value is read from the 'value' field inside the secret data::

        vault kv put secret/MY_SECRET value=s3cr3t

    is retrieved with ``get_secret("MY_SECRET")``.
    """

    def __init__(self) -> None:
        self._addr: str = "http://127.0.0.1:8200"
        self._token: str = ""
        self._namespace: Optional[str] = None
        self._mount: str = "secret"
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "vault"

    @property
    def version(self) -> str:
        return "1.0.0"

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        return {
            "addr": os.getenv("VAULT_ADDR", "http://127.0.0.1:8200"),
            "token": os.getenv("VAULT_TOKEN", ""),
            "namespace": os.getenv("VAULT_NAMESPACE", ""),
            "mount": os.getenv("VAULT_MOUNT", "secret"),
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialise the Vault HTTP client and verify authentication.

        Args:
            config: Dictionary with 'addr', 'token', 'namespace', 'mount'.

        Raises:
            ValueError: If VAULT_TOKEN is missing or authentication fails.
        """
        self._addr = config.get("addr", "http://127.0.0.1:8200").rstrip("/")
        self._token = config.get("token", "")
        self._namespace = config.get("namespace") or None
        self._mount = config.get("mount", "secret")

        if not self._token:
            raise ValueError(
                "VAULT_TOKEN environment variable must be set for the Vault "
                "secret store."
            )

        headers: Dict[str, str] = {"X-Vault-Token": self._token}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace

        self._session = aiohttp.ClientSession(headers=headers)

        # Verify the token is valid by calling the token lookup endpoint.
        url = f"{self._addr}/v1/auth/token/lookup-self"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                await self._session.close()
                self._session = None
                raise ValueError(
                    f"Vault authentication failed (HTTP {resp.status}). "
                    "Check VAULT_ADDR and VAULT_TOKEN."
                )

    async def get_secret(self, key: str) -> str:
        """
        Retrieve a secret from Vault KV v2.

        The *key* is used as the secret path relative to the mount.
        The plugin reads the ``value`` field from the secret's data map.

        Args:
            key: The secret path (e.g. ``"DB_PASSWORD"``).

        Returns:
            The secret value string.

        Raises:
            RuntimeError: If the plugin has not been initialized.
            KeyError: If the secret path does not exist or has no 'value' field.
        """
        if self._session is None:
            raise RuntimeError(
                "VaultSecretStore has not been initialized. "
                "Call initialize() before get_secret()."
            )

        url = f"{self._addr}/v1/{self._mount}/data/{key}"
        async with self._session.get(url) as resp:
            if resp.status == 404:
                raise KeyError(f"Secret '{key}' not found in Vault.")
            if resp.status != 200:
                body = await resp.text()
                raise KeyError(
                    f"Vault returned HTTP {resp.status} for secret '{key}': {body}"
                )
            payload = await resp.json()

        data = payload.get("data", {}).get("data", {})
        if "value" not in data:
            raise KeyError(
                f"Secret '{key}' found in Vault but has no 'value' field. "
                f"Available fields: {list(data.keys())}"
            )
        return str(data["value"])
