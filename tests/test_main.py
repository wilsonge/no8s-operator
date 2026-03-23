"""Unit tests for main.py - Application lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import Application

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db():
    db = AsyncMock()
    db.connect = AsyncMock()
    db.initialize_schema = AsyncMock()
    db.count_users = AsyncMock(return_value=0)
    db.create_user = AsyncMock()
    db.close = AsyncMock()
    return db


def _make_mock_input_plugin():
    plugin = AsyncMock()
    plugin.name = "http"
    plugin.set_db_manager = MagicMock()
    plugin.set_event_bus = MagicMock()
    plugin.set_auth_manager = MagicMock()
    plugin.set_ldap_manager = MagicMock()
    plugin.set_admission_chain = MagicMock()
    plugin.mount_router = MagicMock()
    plugin.start = AsyncMock()
    plugin.stop = AsyncMock()
    return plugin


def _make_mock_registry(input_plugin=None):
    registry = MagicMock()
    registry.list_action_plugins.return_value = []
    registry.list_input_plugins.return_value = ["http"]
    registry.has_input_plugin.return_value = True
    registry.get_input_plugin_config.return_value = {}
    registry.get_action_plugin_config.return_value = {}
    registry.get_input_plugin = AsyncMock(
        return_value=input_plugin or _make_mock_input_plugin()
    )
    registry.get_secret_store = AsyncMock(return_value=AsyncMock())
    return registry


# ---------------------------------------------------------------------------
# Application.__init__
# ---------------------------------------------------------------------------


class TestApplicationInit:
    def test_initial_state(self):
        with patch("main.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            app = Application()

        assert app.db is None
        assert app.controller is None
        assert app.event_bus is None
        assert app.input_plugins == []
        assert app.auth_manager is None
        assert app.ldap_manager is None
        assert app.leader_election is None
        assert app.admission_chain is None
        assert app.running is False


# ---------------------------------------------------------------------------
# Application.initialize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApplicationInitialize:
    """Tests for Application.initialize()."""

    @pytest.fixture
    def mock_config(self):
        cfg = MagicMock()
        cfg.secret_store.plugin = "env"
        cfg.database.host = "localhost"
        cfg.database.port = 5432
        cfg.database.database = "test"
        cfg.database.user = "user"
        cfg.database.password = "pass"
        cfg.database.min_pool_size = 1
        cfg.database.max_pool_size = 5
        cfg.auth.jwt_secret_key = "secret"
        cfg.auth.jwt_expiry_hours = 24
        cfg.auth.initial_admin_username = None
        cfg.auth.initial_admin_password = None
        cfg.ldap.url = ""
        cfg.ldap.bind_dn = ""
        cfg.ldap.bind_password = ""
        cfg.ldap.base_dn = ""
        cfg.ldap.user_filter = ""
        cfg.ldap.attr_username = "uid"
        cfg.ldap.attr_email = "mail"
        cfg.ldap.attr_display_name = "cn"
        cfg.ldap.sync_interval = 0
        cfg.controller.reconcile_interval = 60
        cfg.controller.max_concurrent_reconciles = 5
        cfg.controller.backoff_base_delay = 60
        cfg.controller.backoff_max_delay = 3600
        cfg.controller.backoff_jitter_factor = 0.1
        cfg.plugins.enabled_input_plugins = []
        cfg.plugins.get_plugin_config.return_value = {}
        cfg.leader_election = MagicMock()
        return cfg

    async def test_database_initialized(self, mock_config):
        mock_db = _make_mock_db()
        mock_input_plugin = _make_mock_input_plugin()
        mock_registry = _make_mock_registry(mock_input_plugin)

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_db.connect.assert_called_once()
        mock_db.initialize_schema.assert_called_once()

    async def test_secret_store_initialized(self, mock_config):
        mock_db = _make_mock_db()
        mock_registry = _make_mock_registry()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_registry.get_secret_store.assert_called_once_with("env")

    async def test_auth_manager_created(self, mock_config):
        mock_db = _make_mock_db()
        mock_registry = _make_mock_registry()
        mock_auth = MagicMock()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager", return_value=mock_auth) as mock_auth_cls,
            patch("main.set_auth_manager") as mock_set_auth,
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_auth_cls.assert_called_once_with(
            jwt_secret_key="secret",
            jwt_expiry_hours=24,
        )
        mock_set_auth.assert_called_once_with(mock_auth)
        assert app.auth_manager is mock_auth

    async def test_bootstrap_admin_when_db_empty(self, mock_config):
        mock_config.auth.initial_admin_username = "admin"
        mock_config.auth.initial_admin_password = "password123"

        mock_db = _make_mock_db()
        mock_db.count_users = AsyncMock(return_value=0)
        mock_auth = MagicMock()
        mock_auth.hash_password.return_value = "hashed_pw"
        mock_registry = _make_mock_registry()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager", return_value=mock_auth),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_db.create_user.assert_called_once_with(
            username="admin",
            is_admin=True,
            password_hash="hashed_pw",
            source="manual",
        )

    async def test_bootstrap_admin_skipped_when_users_exist(self, mock_config):
        mock_config.auth.initial_admin_username = "admin"
        mock_config.auth.initial_admin_password = "password123"

        mock_db = _make_mock_db()
        mock_db.count_users = AsyncMock(return_value=1)
        mock_registry = _make_mock_registry()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_db.create_user.assert_not_called()

    async def test_bootstrap_admin_skipped_when_no_credentials(self, mock_config):
        """No admin created if initial_admin_username/password not configured."""
        mock_db = _make_mock_db()
        mock_db.count_users = AsyncMock(return_value=0)
        mock_registry = _make_mock_registry()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_db.create_user.assert_not_called()

    async def test_input_plugin_initialized_with_dependencies(self, mock_config):
        mock_db = _make_mock_db()
        mock_input_plugin = _make_mock_input_plugin()
        mock_registry = _make_mock_registry(mock_input_plugin)
        mock_auth = MagicMock()
        mock_ldap = MagicMock()
        mock_ldap.is_configured.return_value = True

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager", return_value=mock_auth),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager", return_value=mock_ldap),
            patch("main.EventBus") as mock_event_bus_cls,
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain") as mock_admission_chain_cls,
            patch("main.create_management_router") as mock_create_mgmt_router,
        ):
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus
            mock_admission_chain = MagicMock()
            mock_admission_chain_cls.return_value = mock_admission_chain

            app = Application()
            await app.initialize()

        mock_input_plugin.set_db_manager.assert_called_once_with(mock_db)
        mock_input_plugin.set_event_bus.assert_called_once_with(mock_event_bus)
        mock_input_plugin.set_auth_manager.assert_called_once_with(mock_auth)
        mock_input_plugin.set_ldap_manager.assert_called_once_with(mock_ldap)
        mock_input_plugin.set_admission_chain.assert_called_once_with(
            mock_admission_chain
        )
        # mount_router should be called twice: cluster_status + management
        assert mock_input_plugin.mount_router.call_count == 2
        mock_admission_chain_cls.assert_called_once_with(mock_db)
        mock_create_mgmt_router.assert_called_once_with(
            db_manager=mock_db,
            auth_manager=mock_auth,
            ldap_manager=mock_ldap,
            event_bus=mock_event_bus,
            admission_chain=mock_admission_chain,
        )
        assert mock_input_plugin in app.input_plugins

    async def test_unknown_input_plugin_skipped(self, mock_config):
        mock_config.plugins.enabled_input_plugins = ["nonexistent"]
        mock_db = _make_mock_db()
        mock_registry = _make_mock_registry()
        mock_registry.has_input_plugin.return_value = False

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        assert app.input_plugins == []

    async def test_controller_created(self, mock_config):
        mock_db = _make_mock_db()
        mock_registry = _make_mock_registry()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection"),
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        assert app.controller is not None

    async def test_leader_election_created(self, mock_config):
        mock_db = _make_mock_db()
        mock_registry = _make_mock_registry()
        mock_le = MagicMock()

        with (
            patch("main.get_config", return_value=mock_config),
            patch("main.register_builtin_plugins"),
            patch("main.get_registry", return_value=mock_registry),
            patch("main.DatabaseManager", return_value=mock_db),
            patch("main.AuthManager"),
            patch("main.set_auth_manager"),
            patch("main.LDAPSyncManager") as mock_ldap_cls,
            patch("main.EventBus"),
            patch("main.LeaderElection", return_value=mock_le) as mock_le_cls,
            patch("main.create_cluster_status_router"),
            patch("main.AdmissionChain"),
            patch("main.create_management_router"),
        ):
            mock_ldap_cls.return_value.is_configured.return_value = False
            app = Application()
            await app.initialize()

        mock_le_cls.assert_called_once_with(
            db=mock_db, config=mock_config.leader_election
        )
        assert app.leader_election is mock_le


# ---------------------------------------------------------------------------
# Application.stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApplicationStop:
    async def test_stop_sets_running_false(self):
        with patch("main.get_config"):
            app = Application()
        app.running = True
        await app.stop()
        assert app.running is False

    async def test_stop_closes_db(self):
        with patch("main.get_config"):
            app = Application()
        mock_db = AsyncMock()
        app.db = mock_db
        app.running = True
        await app.stop()
        mock_db.close.assert_called_once()

    async def test_stop_stops_controller(self):
        with patch("main.get_config"):
            app = Application()
        mock_controller = AsyncMock()
        app.controller = mock_controller
        app.running = True
        await app.stop()
        mock_controller.stop.assert_called_once()

    async def test_stop_stops_all_input_plugins(self):
        with patch("main.get_config"):
            app = Application()
        plugin_a = AsyncMock()
        plugin_b = AsyncMock()
        app.input_plugins = [plugin_a, plugin_b]
        app.running = True
        await app.stop()
        plugin_a.stop.assert_called_once()
        plugin_b.stop.assert_called_once()

    async def test_stop_stops_leader_election(self):
        with patch("main.get_config"):
            app = Application()
        mock_le = AsyncMock()
        app.leader_election = mock_le
        app.running = True
        await app.stop()
        mock_le.stop.assert_called_once()

    async def test_stop_with_no_components_is_safe(self):
        """stop() does not raise if components are None."""
        with patch("main.get_config"):
            app = Application()
        app.running = True
        # All components are None — should not raise
        await app.stop()


# ---------------------------------------------------------------------------
# Application._ldap_sync_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLDAPSyncLoop:
    async def test_sync_loop_calls_sync_to_db(self):
        with patch("main.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            mock_cfg.return_value.ldap.sync_interval = 1
            app = Application()

        mock_ldap = AsyncMock()
        mock_ldap.sync_to_db = AsyncMock(return_value={"created": 0, "updated": 0})
        mock_db = AsyncMock()
        app.ldap_manager = mock_ldap
        app.db = mock_db
        app.running = True

        async def stop_after_one_sync(*args, **kwargs):
            app.running = False
            await asyncio.sleep(0)

        mock_ldap.sync_to_db.side_effect = stop_after_one_sync

        await app._ldap_sync_loop()

        mock_ldap.sync_to_db.assert_called_once_with(mock_db)

    async def test_sync_loop_continues_on_error(self):
        with patch("main.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            mock_cfg.return_value.ldap.sync_interval = 0
            app = Application()

        mock_ldap = AsyncMock()
        call_count = 0

        async def flaky_sync(db):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("LDAP unavailable")
            app.running = False

        mock_ldap.sync_to_db.side_effect = flaky_sync
        app.ldap_manager = mock_ldap
        app.db = AsyncMock()
        app.running = True

        await app._ldap_sync_loop()

        assert call_count == 2


# ---------------------------------------------------------------------------
# Application.start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApplicationStart:
    async def test_start_calls_initialize_if_not_ready(self):
        with patch("main.get_config"):
            app = Application()

        app.initialize = AsyncMock()
        app.running = False
        app.leader_election = AsyncMock()
        app.controller = AsyncMock()
        app.input_plugins = []

        # Patch gather to return immediately so start() completes
        with patch("main.asyncio.gather", new_callable=AsyncMock):
            await app.start()

        app.initialize.assert_called_once()

    async def test_start_does_not_call_initialize_when_ready(self):
        with patch("main.get_config"):
            app = Application()

        app.initialize = AsyncMock()
        app.controller = AsyncMock()
        app.input_plugins = [AsyncMock()]
        app.leader_election = AsyncMock()

        with patch("main.asyncio.gather", new_callable=AsyncMock):
            await app.start()

        app.initialize.assert_not_called()

    async def test_on_resource_event_publishes_trigger(self):
        """on_resource_event publishes a TRIGGER event to the event bus."""
        from events import EventBus, EventType

        with patch("main.get_config"):
            app = Application()

        bus = EventBus()
        app.event_bus = bus
        app.controller = AsyncMock()
        app.input_plugins = []
        app.leader_election = AsyncMock()
        app.initialize = AsyncMock()

        _, sub = await bus.subscribe(
            filter_fn=lambda e: e.event_type == EventType.TRIGGER
        )

        spec = MagicMock()
        spec.name = "my-db"
        spec.resource_type_name = "DatabaseCluster"

        original_create_task = asyncio.create_task

        def tracking_create_task(coro, **kwargs):
            return original_create_task(coro, **kwargs)

        async def fake_gather(*tasks):
            # Execute the on_resource_event callback that was passed to start()
            pass

        with (
            patch("main.asyncio.gather", new_callable=AsyncMock),
            patch("main.asyncio.create_task", side_effect=tracking_create_task),
        ):
            # We need to intercept the on_resource_event closure
            # Directly test by calling start and extracting via plugin.start
            plugin = AsyncMock()
            plugin.start = AsyncMock()
            app.input_plugins = [plugin]
            await app.start()

        # Extract the callback passed to plugin.start
        assert plugin.start.called
        callback = plugin.start.call_args[0][0]

        # Call the callback and check that a TRIGGER is published
        await callback("CREATED", spec)

        import asyncio as _asyncio

        received = await _asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert received.event_type == EventType.TRIGGER
        assert received.resource_type_name == "DatabaseCluster"
        assert received.resource_name == "my-db"

    async def test_on_resource_event_no_bus_is_safe(self):
        """on_resource_event does not raise when event_bus is None."""
        with patch("main.get_config"):
            app = Application()

        app.event_bus = None
        app.controller = AsyncMock()
        app.input_plugins = []
        app.leader_election = AsyncMock()
        app.initialize = AsyncMock()

        plugin = AsyncMock()
        plugin.start = AsyncMock()
        app.input_plugins = [plugin]

        with patch("main.asyncio.gather", new_callable=AsyncMock):
            await app.start()

        callback = plugin.start.call_args[0][0]
        spec = MagicMock()
        spec.name = "r"
        spec.resource_type_name = "T"
        # Should not raise
        await callback("MODIFIED", spec)

    async def test_start_creates_ldap_task_when_configured(self):
        with patch("main.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            mock_cfg.return_value.ldap.sync_interval = 60
            app = Application()

        app.controller = AsyncMock()
        app.input_plugins = [AsyncMock()]
        app.leader_election = AsyncMock()

        mock_ldap = MagicMock()
        mock_ldap.is_configured.return_value = True
        app.ldap_manager = mock_ldap

        sync_loop_called = False

        async def fake_sync_loop():
            nonlocal sync_loop_called
            sync_loop_called = True

        app._ldap_sync_loop = fake_sync_loop
        app.running = False
        app.initialize = AsyncMock()

        # Intercept create_task to track which coroutines are scheduled
        created_coros = []
        original_create_task = asyncio.create_task

        def tracking_create_task(coro, **kwargs):
            created_coros.append(coro)
            return original_create_task(coro, **kwargs)

        with (
            patch("main.asyncio.gather", new_callable=AsyncMock),
            patch("main.asyncio.create_task", side_effect=tracking_create_task),
        ):
            await app.start()

        # The LDAP sync loop coroutine should have been scheduled
        assert sync_loop_called or any(
            hasattr(c, "__name__") and "sync" in getattr(c, "__name__", "")
            for c in created_coros
        ), "Expected LDAP sync loop to be included in tasks"
