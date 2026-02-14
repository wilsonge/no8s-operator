"""
Main entry point for the Operator Controller.

This module initializes and starts the controller with the plugin-based architecture.
"""

import asyncio
import logging
import signal
from typing import Any, Dict, List, Optional

from config import get_config
from controller import Controller, ControllerConfig
from db import DatabaseManager
from events import EventBus
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
        self.running = False

    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing Operator Controller")

        # Register built-in plugins
        register_builtin_plugins()
        registry = get_registry()

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
            self.input_plugins.append(plugin)
            logger.info(f"Initialized input plugin: {plugin_name}")

        logger.info("All components initialized")

    async def start(self):
        """Start the application."""
        if not self.controller or not self.input_plugins:
            await self.initialize()

        self.running = True
        logger.info("Starting Operator Controller")

        # Resource event callback (for future use when input plugins notify controller)
        async def on_resource_event(event_type: str, spec):
            logger.debug(f"Resource event: {event_type} - {spec.name}")
            # Controller already polls database, so no immediate action needed
            # This callback can be used for more immediate reconciliation in the future

        # Start controller and all input plugins concurrently
        tasks = [asyncio.create_task(self.controller.start())]
        for plugin in self.input_plugins:
            tasks.append(asyncio.create_task(plugin.start(on_resource_event)))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Application tasks cancelled")

    async def stop(self):
        """Stop the application gracefully."""
        logger.info("Stopping Operator Controller")
        self.running = False

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
