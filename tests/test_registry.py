"""Unit tests for plugins/registry.py - PluginRegistry action/input/secret coverage."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.registry import PluginRegistry, get_registry, reset_registry
from plugins.actions.base import ActionPlugin
from plugins.inputs.base import InputPlugin
from plugins.secrets.base import SecretStorePlugin


# ---------------------------------------------------------------------------
# Minimal concrete implementations for testing
# ---------------------------------------------------------------------------


class DummyActionPlugin(ActionPlugin):
    @property
    def name(self):
        return "dummy_action"

    @property
    def version(self):
        return "1.0.0"

    async def initialize(self, config):
        self._config = config

    async def validate_spec(self, spec):
        return True, None

    async def prepare(self, ctx):
        return "/workspace"

    async def plan(self, ctx, workspace):
        pass

    async def apply(self, ctx, workspace):
        pass

    async def destroy(self, ctx, workspace):
        pass

    async def get_outputs(self, ctx, workspace):
        return {}

    async def get_state(self, ctx, workspace):
        return None

    async def cleanup(self, workspace):
        pass


class DummyInputPlugin(InputPlugin):
    @property
    def name(self):
        return "dummy_input"

    @property
    def version(self):
        return "1.0.0"

    async def initialize(self, config):
        self._config = config

    async def start(self, on_resource_event):
        pass

    async def stop(self):
        pass

    async def health_check(self):
        return True, "ok"


class DummySecretStore(SecretStorePlugin):
    @property
    def name(self):
        return "dummy_store"

    @property
    def version(self):
        return "1.0.0"

    async def initialize(self, config):
        pass

    async def get_secret(self, key):
        return "secret_value"


class AnotherActionPlugin(ActionPlugin):
    @property
    def name(self):
        return "another_action"

    @property
    def version(self):
        return "2.0.0"

    async def initialize(self, config):
        pass

    async def validate_spec(self, spec):
        return True, None

    async def prepare(self, ctx):
        return None

    async def plan(self, ctx, workspace):
        pass

    async def apply(self, ctx, workspace):
        pass

    async def destroy(self, ctx, workspace):
        pass

    async def get_outputs(self, ctx, workspace):
        return {}

    async def get_state(self, ctx, workspace):
        return None

    async def cleanup(self, workspace):
        pass


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_registry():
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# Action plugin registration
# ---------------------------------------------------------------------------


class TestActionPluginRegistration:
    def test_register_action_plugin(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        assert "dummy_action" in registry.list_action_plugins()

    def test_register_action_plugin_caches_info(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        info = registry.get_action_plugin_info("dummy_action")
        assert info == {"name": "dummy_action", "version": "1.0.0"}

    def test_register_multiple_action_plugins(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        registry.register_action_plugin(AnotherActionPlugin)
        plugins = registry.list_action_plugins()
        assert "dummy_action" in plugins
        assert "another_action" in plugins

    def test_overwrite_action_plugin_warns(self, caplog):
        import logging
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        with caplog.at_level(logging.WARNING):
            registry.register_action_plugin(DummyActionPlugin)
        assert any("Overwriting" in r.message for r in caplog.records)

    def test_has_action_plugin_true(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        assert registry.has_action_plugin("dummy_action") is True

    def test_has_action_plugin_false(self):
        registry = PluginRegistry()
        assert registry.has_action_plugin("nonexistent") is False

    def test_get_action_plugin_info_not_found(self):
        registry = PluginRegistry()
        assert registry.get_action_plugin_info("missing") is None

    def test_get_action_plugin_config_empty_by_default(self):
        registry = PluginRegistry()
        assert registry.get_action_plugin_config("missing") == {}

    def test_get_action_plugin_config_loaded_on_registration(self):
        registry = PluginRegistry()
        with patch.object(DummyActionPlugin, "load_config_from_env", return_value={"key": "val"}):
            registry.register_action_plugin(DummyActionPlugin)
        assert registry.get_action_plugin_config("dummy_action") == {"key": "val"}


@pytest.mark.asyncio
class TestActionPluginInstantiation:
    async def test_get_action_plugin_initializes(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        plugin = await registry.get_action_plugin("dummy_action", {"foo": "bar"})
        assert isinstance(plugin, DummyActionPlugin)

    async def test_get_action_plugin_cached(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        p1 = await registry.get_action_plugin("dummy_action")
        p2 = await registry.get_action_plugin("dummy_action")
        assert p1 is p2

    async def test_get_action_plugin_passes_config(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        plugin = await registry.get_action_plugin("dummy_action", {"timeout": 30})
        assert plugin._config == {"timeout": 30}

    async def test_get_unknown_action_plugin_raises(self):
        registry = PluginRegistry()
        with pytest.raises(ValueError, match="Unknown action plugin"):
            await registry.get_action_plugin("nonexistent")

    async def test_get_unknown_action_plugin_lists_available(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        with pytest.raises(ValueError, match="dummy_action"):
            await registry.get_action_plugin("nonexistent")

    async def test_get_action_plugin_no_config_uses_empty_dict(self):
        registry = PluginRegistry()
        registry.register_action_plugin(DummyActionPlugin)
        plugin = await registry.get_action_plugin("dummy_action")
        assert isinstance(plugin, DummyActionPlugin)


# ---------------------------------------------------------------------------
# Input plugin registration
# ---------------------------------------------------------------------------


class TestInputPluginRegistration:
    def test_register_input_plugin(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        assert "dummy_input" in registry.list_input_plugins()

    def test_register_input_plugin_caches_info(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        info = registry.get_input_plugin_info("dummy_input")
        assert info == {"name": "dummy_input", "version": "1.0.0"}

    def test_has_input_plugin_true(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        assert registry.has_input_plugin("dummy_input") is True

    def test_has_input_plugin_false(self):
        registry = PluginRegistry()
        assert registry.has_input_plugin("nonexistent") is False

    def test_get_input_plugin_info_not_found(self):
        registry = PluginRegistry()
        assert registry.get_input_plugin_info("missing") is None

    def test_get_input_plugin_config_empty_by_default(self):
        registry = PluginRegistry()
        assert registry.get_input_plugin_config("missing") == {}

    def test_overwrite_input_plugin_warns(self, caplog):
        import logging
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        with caplog.at_level(logging.WARNING):
            registry.register_input_plugin(DummyInputPlugin)
        assert any("Overwriting" in r.message for r in caplog.records)

    def test_list_input_plugins_empty(self):
        registry = PluginRegistry()
        assert registry.list_input_plugins() == []


@pytest.mark.asyncio
class TestInputPluginInstantiation:
    async def test_get_input_plugin_initializes(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        plugin = await registry.get_input_plugin("dummy_input", {})
        assert isinstance(plugin, DummyInputPlugin)

    async def test_get_input_plugin_cached(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        p1 = await registry.get_input_plugin("dummy_input")
        p2 = await registry.get_input_plugin("dummy_input")
        assert p1 is p2

    async def test_get_unknown_input_plugin_raises(self):
        registry = PluginRegistry()
        with pytest.raises(ValueError, match="Unknown input plugin"):
            await registry.get_input_plugin("nonexistent")

    async def test_get_unknown_input_plugin_lists_available(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        with pytest.raises(ValueError, match="dummy_input"):
            await registry.get_input_plugin("nonexistent")

    async def test_get_input_plugin_passes_config(self):
        registry = PluginRegistry()
        registry.register_input_plugin(DummyInputPlugin)
        plugin = await registry.get_input_plugin("dummy_input", {"port": 8080})
        assert plugin._config == {"port": 8080}


# ---------------------------------------------------------------------------
# Secret store plugin registration
# ---------------------------------------------------------------------------


class TestSecretStorePluginRegistration:
    def test_register_secret_store_plugin(self):
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        assert "dummy_store" in registry.list_secret_store_plugins()

    def test_overwrite_secret_store_warns(self, caplog):
        import logging
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        with caplog.at_level(logging.WARNING):
            registry.register_secret_store_plugin(DummySecretStore)
        assert any("Overwriting" in r.message for r in caplog.records)

    def test_list_secret_store_plugins_empty(self):
        registry = PluginRegistry()
        assert registry.list_secret_store_plugins() == []


@pytest.mark.asyncio
class TestSecretStoreInstantiation:
    async def test_get_secret_store_initializes(self):
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        store = await registry.get_secret_store("dummy_store")
        assert isinstance(store, DummySecretStore)

    async def test_get_secret_store_cached(self):
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        s1 = await registry.get_secret_store("dummy_store")
        s2 = await registry.get_secret_store()
        assert s1 is s2

    async def test_get_secret_store_sets_active_name(self):
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        await registry.get_secret_store("dummy_store")
        assert registry._active_secret_store_name == "dummy_store"

    async def test_get_unknown_secret_store_raises(self):
        registry = PluginRegistry()
        with pytest.raises(ValueError, match="Unknown secret store plugin"):
            await registry.get_secret_store("nonexistent")

    async def test_get_unknown_secret_store_lists_available(self):
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        with pytest.raises(ValueError, match="dummy_store"):
            await registry.get_secret_store("nonexistent")

    async def test_second_call_returns_cached_instance(self):
        """Subsequent calls without name return the already-initialized store."""
        registry = PluginRegistry()
        registry.register_secret_store_plugin(DummySecretStore)
        s1 = await registry.get_secret_store("dummy_store")
        # Change name but cached instance should still be returned
        s2 = await registry.get_secret_store()
        assert s1 is s2


# ---------------------------------------------------------------------------
# Global registry singleton
# ---------------------------------------------------------------------------


class TestGlobalRegistrySingleton:
    def test_get_registry_returns_same_instance(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_registry_creates_new_instance(self):
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2

    def test_reset_registry_clears_plugins(self):
        registry = get_registry()
        registry.register_action_plugin(DummyActionPlugin)
        assert "dummy_action" in registry.list_action_plugins()

        reset_registry()
        new_registry = get_registry()
        assert "dummy_action" not in new_registry.list_action_plugins()


# ---------------------------------------------------------------------------
# Module-level get_secret_store convenience wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestModuleLevelGetSecretStore:
    async def test_module_get_secret_store_delegates_to_registry(self):
        from plugins.registry import get_secret_store

        registry = get_registry()
        registry.register_secret_store_plugin(DummySecretStore)
        await registry.get_secret_store("dummy_store")

        store = await get_secret_store()
        assert isinstance(store, DummySecretStore)