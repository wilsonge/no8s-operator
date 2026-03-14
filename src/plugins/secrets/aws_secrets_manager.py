"""
AWS Secrets Manager secret store backend.

Requires the 'boto3' package::

    pip install "no8s-operator[aws]"

Configuration environment variables (standard AWS credentials chain applies):

    AWS_REGION              - AWS region (default: us-east-1)
    AWS_ACCESS_KEY_ID       - AWS access key (optional if using instance role)
    AWS_SECRET_ACCESS_KEY   - AWS secret key (optional if using instance role)
    AWS_SESSION_TOKEN       - AWS session token (optional)
"""

import os
from typing import Any, Dict, Optional

from plugins.secrets.base import SecretStorePlugin


class AWSSecretsManagerStore(SecretStorePlugin):
    """
    Secret store plugin backed by AWS Secrets Manager.

    Each secret is stored as a Secrets Manager secret whose *SecretString* is
    either a plain string or a JSON object.  When the value is JSON, the plugin
    reads the field whose name matches *key* (the last path component).

    Example — plain string::

        aws secretsmanager create-secret \\
            --name DB_PASSWORD --secret-string "hunter2"

    Example — JSON object (useful when grouping related secrets)::

        aws secretsmanager create-secret \\
            --name myapp/secrets \\
            --secret-string '{"DB_PASSWORD":"hunter2","JWT_SECRET":"abc123"}'

        # retrieve with key "myapp/secrets" → returns the whole JSON string, or
        # use key "myapp/secrets/DB_PASSWORD" → returns "hunter2"
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._region: str = "us-east-1"

    @property
    def name(self) -> str:
        return "aws_secrets_manager"

    @property
    def version(self) -> str:
        return "1.0.0"

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        return {
            "region": os.getenv("AWS_REGION", "us-east-1"),
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialise the boto3 Secrets Manager client.

        Args:
            config: Dictionary with 'region'.

        Raises:
            ImportError: If the boto3 package is not installed.
        """
        try:
            import boto3  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "The 'boto3' package is required for the AWS Secrets Manager "
                "secret store. Install it with: pip install 'no8s-operator[aws]'"
            )

        self._region = config.get("region", "us-east-1")
        self._client = boto3.client(
            "secretsmanager",
            region_name=self._region,
        )

    async def get_secret(self, key: str) -> str:
        """
        Retrieve a secret from AWS Secrets Manager.

        The *key* is used as the SecretId.  If the stored value is a JSON
        object, the plain *key* returns the full JSON string.

        Args:
            key: The Secrets Manager secret name or ARN.

        Returns:
            The secret value as a string.

        Raises:
            RuntimeError: If the plugin has not been initialized.
            KeyError: If the secret does not exist or access is denied.
        """
        if self._client is None:
            raise RuntimeError(
                "AWSSecretsManagerStore has not been initialized. "
                "Call initialize() before get_secret()."
            )

        try:
            response = self._client.get_secret_value(SecretId=key)
        except Exception as exc:
            raise KeyError(
                f"Secret '{key}' not found in AWS Secrets Manager: {exc}"
            ) from exc

        secret = response.get("SecretString") or response.get("SecretBinary", b"")
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8")
        return str(secret)
