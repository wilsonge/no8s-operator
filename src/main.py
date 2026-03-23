"""
Main entry point for the Operator Controller.

This module initializes and starts the controller with the plugin-based architecture.
"""

import asyncio
import logging
import signal
from typing import Any, Dict, List, Optional

from admission import AdmissionChain
from auth import AuthManager, set_auth_manager
from cluster_status import create_cluster_status_router
from config import get_config
from controller import Controller, ControllerConfig
from db import DatabaseManager
from events import EventBus
from ldap_sync import LDAPSyncManager
from leader_election import LeaderElection
from management_api import create_management_router
from plugins.registry import get_registry, register_builtin_plugins
from plugins.inputs.base import InputPlugin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Application:
    """Main application that orchestrates the controller and plugins."""

    def __init__(self):
        self.config = get_config()
        self.db: Optional[DatabaseManager] = None
        self.controller: Optional[Controller] = None
        self.event_bus: Optional[EventBus] = None
        self.input_plugins: List[InputPlugin] = []
        self.auth_manager: Optional[AuthManager] = None
        self.ldap_manager: Optional[LDAPSyncManager] = None
        self.leader_election: Optional[LeaderElection] = None
        self.admission_chain: Optional[AdmissionChain] = None
        self.running = False

    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing Operator Controller")

        # Register built-in plugins (including secret store backends)
        register_builtin_plugins()
        registry = get_registry()

        # Initialize the configured secret store before anything else so that
        # other components can retrieve secrets at startup.
        await registry.get_secret_store(self.config.secret_store.plugin)
        logger.info("Secret store initialized: %s", self.config.secret_store.plugin)

        # Initialize database
        db_config = self.config.database
        self.db = DatabaseManager(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user=db_config.user,
            password=db_config.password,
            min_pool_size=db_config.min_pool_size,
            max_pool_size=db_config.max_pool_size,
        )
        await self.db.connect()
        await self.db.initialize_schema()
        logger.info("Database initialized")

        # Initialize auth
        auth_cfg = self.config.auth
        self.auth_manager = AuthManager(
            jwt_secret_key=auth_cfg.jwt_secret_key,
            jwt_expiry_hours=auth_cfg.jwt_expiry_hours,
        )
        set_auth_manager(self.auth_manager)

        # Bootstrap initial admin user if DB is empty
        if (
            auth_cfg.initial_admin_username
            and auth_cfg.initial_admin_password
            and await self.db.count_users() == 0
        ):
            pw_hash = self.auth_manager.hash_password(auth_cfg.initial_admin_password)
            await self.db.create_user(
                username=auth_cfg.initial_admin_username,
                is_admin=True,
                password_hash=pw_hash,
                source="manual",
            )
            logger.info(
                "Bootstrapped initial admin user: %s",
                auth_cfg.initial_admin_username,
            )

        # Initialize LDAP manager (optional)
        from ldap_sync import LDAPConfig as LDAPSyncConfig

        ldap_cfg = self.config.ldap
        ldap_sync_cfg = LDAPSyncConfig(
            url=ldap_cfg.url,
            bind_dn=ldap_cfg.bind_dn,
            bind_password=ldap_cfg.bind_password,
            base_dn=ldap_cfg.base_dn,
            user_filter=ldap_cfg.user_filter,
            attr_username=ldap_cfg.attr_username,
            attr_email=ldap_cfg.attr_email,
            attr_display_name=ldap_cfg.attr_display_name,
            sync_interval=ldap_cfg.sync_interval,
        )
        self.ldap_manager = LDAPSyncManager(ldap_sync_cfg)
        if self.ldap_manager.is_configured():
            logger.info("LDAP configured: %s", ldap_cfg.url)

        # Initialize event bus
        self.event_bus = EventBus()

        # Build plugin configs from registry (plugins define their own env loading)
        # Config can override with PLUGIN_CONFIGS env var
        plugin_configs: Dict[str, Dict[str, Any]] = {}
        for plugin_name in registry.list_action_plugins():
            plugin_configs[plugin_name] = registry.get_action_plugin_config(plugin_name)
            # Allow config overrides from PLUGIN_CONFIGS
            plugin_configs[plugin_name].update(
                self.config.plugins.get_plugin_config(plugin_name)
            )

        # Create controller configuration
        ctrl_config = self.config.controller
        controller_config = ControllerConfig(
            reconcile_interval=ctrl_config.reconcile_interval,
            max_concurrent_reconciles=ctrl_config.max_concurrent_reconciles,
            backoff_base_delay=ctrl_config.backoff_base_delay,
            backoff_max_delay=ctrl_config.backoff_max_delay,
            backoff_jitter_factor=ctrl_config.backoff_jitter_factor,
            plugin_configs=plugin_configs,
        )

        # Initialize controller
        self.controller = Controller(
            db_manager=self.db,
            registry=registry,
            config=controller_config,
            event_bus=self.event_bus,
        )

        # Initialize leader election
        le_cfg = self.config.leader_election
        self.leader_election = LeaderElection(db=self.db, config=le_cfg)

        # Build core routers to be mounted before plugins are initialized
        cluster_status_router = create_cluster_status_router(
            leader_election=self.leader_election,
            db_manager=self.db,
        )

        self.admission_chain = AdmissionChain(self.db)
        management_router = create_management_router(
            db_manager=self.db,
            auth_manager=self.auth_manager,
            ldap_manager=self.ldap_manager,
            event_bus=self.event_bus,
            admission_chain=self.admission_chain,
        )

        # Determine which input plugins to load
        enabled_inputs = self.config.plugins.enabled_input_plugins
        if not enabled_inputs:
            # If not specified, use all registered input plugins
            enabled_inputs = registry.list_input_plugins()

        # Initialize all enabled input plugins
        for plugin_name in enabled_inputs:
            if not registry.has_input_plugin(plugin_name):
                logger.warning(f"Input plugin '{plugin_name}' not found, skipping")
                continue

            # Get plugin config from registry (env-loaded) with config overrides
            plugin_config = registry.get_input_plugin_config(plugin_name)
            plugin_config.update(self.config.plugins.get_plugin_config(plugin_name))

            plugin = await registry.get_input_plugin(plugin_name, plugin_config)
            plugin.set_db_manager(self.db)
            plugin.set_event_bus(self.event_bus)
            if hasattr(plugin, "set_auth_manager") and self.auth_manager:
                plugin.set_auth_manager(self.auth_manager)
            if hasattr(plugin, "set_ldap_manager") and self.ldap_manager:
                plugin.set_ldap_manager(self.ldap_manager)
            if hasattr(plugin, "set_admission_chain"):
                plugin.set_admission_chain(self.admission_chain)
            plugin.mount_router(cluster_status_router)
            plugin.mount_router(management_router)
            self.input_plugins.append(plugin)
            logger.info(f"Initialized input plugin: {plugin_name}")

        logger.info("All components initialized")

    async def start(self):
        """Start the application."""
        if not self.controller or not self.input_plugins:
            await self.initialize()

        self.running = True
        logger.info("Starting Operator Controller")

        # Resource event callback — publishes a TRIGGER to wake reconcilers immediately
        async def on_resource_event(event_type: str, spec):
            logger.debug(f"Resource event: {event_type} - {spec.name}")
            if self.event_bus:
                from datetime import datetime
                from events import EventType, ResourceEvent

                event = ResourceEvent(
                    event_type=EventType.TRIGGER,
                    resource_id=0,
                    resource_name=spec.name,
                    resource_type_name=spec.resource_type_name,
                    resource_type_version="",
                    resource_data={},
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )
                await self.event_bus.publish(event)

        # Start leader election wrapping the controller (only leader reconciles)
        async def run_with_election():
            await self.leader_election.run(
                on_started_leading=self.controller.start,
                on_stopped_leading=self.controller.stop,
            )

        tasks = [asyncio.create_task(run_with_election())]
        # Input plugins (HTTP API) run on all instances regardless of leadership
        for plugin in self.input_plugins:
            tasks.append(asyncio.create_task(plugin.start(on_resource_event)))

        # Optional periodic LDAP sync
        if (
            self.ldap_manager
            and self.ldap_manager.is_configured()
            and self.config.ldap.sync_interval > 0
        ):
            tasks.append(asyncio.create_task(self._ldap_sync_loop()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Application tasks cancelled")

    async def _ldap_sync_loop(self) -> None:
        """Periodically synchronise LDAP users into the DB."""
        interval = self.config.ldap.sync_interval
        logger.info("Starting LDAP sync loop (interval=%ds)", interval)
        while self.running:
            try:
                stats = await self.ldap_manager.sync_to_db(self.db)
                logger.info("LDAP sync: %s", stats)
            except Exception as exc:
                logger.error("LDAP sync error: %s", exc)
            await asyncio.sleep(interval)

    async def stop(self):
        """Stop the application gracefully."""
        logger.info("Stopping Operator Controller")
        self.running = False

        if self.leader_election:
            await self.leader_election.stop()

        if self.controller:
            await self.controller.stop()

        for plugin in self.input_plugins:
            await plugin.stop()

        if self.db:
            await self.db.close()

        logger.info("Operator Controller stopped")


async def main():
    """Main entry point."""
    app = Application()

    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(app.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
