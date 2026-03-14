"""Unit tests for secret store plugins."""

import os
import pytest
from unittest.mock import patch

from plugins.secrets.base import SecretStorePlugin
from plugins.secrets.env import EnvSecretStore


# ---------------------------------------------------------------------------
# SecretStorePlugin ABC
# ---------------------------------------------------------------------------


class TestSecretStorePluginABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SecretStorePlugin()

    def test_incomplete_subclass_raises(self):
        class Incomplete(SecretStorePlugin):
            @property
            def name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_works(self):
        class Concrete(SecretStorePlugin):
            @property
            def name(self):
                return "concrete"

            @property
            def version(self):
                return "1.0.0"

            async def initialize(self, config):
                pass

            async def get_secret(self, key):
                return "value"

        store = Concrete()
        assert store.name == "concrete"
        assert store.version == "1.0.0"

    def test_load_config_from_env_returns_empty_dict_by_default(self):
        class Concrete(SecretStorePlugin):
            @property
            def name(self):
                return "concrete"

            @property
            def version(self):
                return "1.0.0"

            async def initialize(self, config):
                pass

            async def get_secret(self, key):
                return "value"

        assert Concrete.load_config_from_env() == {}


# ---------------------------------------------------------------------------
# EnvSecretStore
# ---------------------------------------------------------------------------


class TestEnvSecretStore:
    @pytest.fixture
    def store(self):
        return EnvSecretStore()

    def test_name(self, store):
        assert store.name == "env"

    def test_version(self, store):
        assert store.version == "1.0.0"

    async def test_initialize_is_noop(self, store):
        # Should not raise and needs no external resources
        await store.initialize({})
        await store.initialize({"irrelevant": "config"})

    async def test_get_secret_returns_env_var(self, store):
        with patch.dict(os.environ, {"MY_SECRET": "supersecret"}):
            value = await store.get_secret("MY_SECRET")
        assert value == "supersecret"

    async def test_get_secret_raises_key_error_when_missing(self, store):
        # Ensure the var is not set
        env_without_key = {k: v for k, v in os.environ.items() if k != "MISSING_VAR"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises(KeyError, match="MISSING_VAR"):
                await store.get_secret("MISSING_VAR")

    async def test_get_secret_raises_key_error_when_empty(self, store):
        with patch.dict(os.environ, {"EMPTY_VAR": ""}):
            with pytest.raises(KeyError, match="EMPTY_VAR"):
                await store.get_secret("EMPTY_VAR")

    async def test_get_secret_error_message_includes_key(self, store):
        env_without_key = {k: v for k, v in os.environ.items() if k != "SOME_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            try:
                await store.get_secret("SOME_KEY")
                pytest.fail("Expected KeyError")
            except KeyError as exc:
                assert "SOME_KEY" in str(exc)

    async def test_get_secret_with_special_characters(self, store):
        with patch.dict(os.environ, {"DB_URL": "postgresql://user:p@ss!@host/db"}):
            value = await store.get_secret("DB_URL")
        assert value == "postgresql://user:p@ss!@host/db"

    def test_load_config_from_env_returns_empty_dict(self):
        assert EnvSecretStore.load_config_from_env() == {}

    def test_is_instance_of_base(self):
        assert isinstance(EnvSecretStore(), SecretStorePlugin)