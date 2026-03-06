"""
Distributed leader election for the Operator Controller.

Uses the `locks` table to ensure only one controller instance actively
reconciles at a time. All instances compete for the lock; the winner
runs the reconciliation loop while others idle and retry.

Input plugins (HTTP API) run on all instances regardless of leadership.
"""

import asyncio
import logging
import os
import socket
from typing import Callable, Coroutine, Optional
from uuid import uuid4

from config import LeaderElectionConfig
from db import DatabaseManager

logger = logging.getLogger(__name__)


def _default_holder_id() -> str:
    """Generate a unique holder ID for this process."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


class LeaderElection:
    """Manages distributed leader election via a database lock."""

    def __init__(self, db: DatabaseManager, config: LeaderElectionConfig):
        self._db = db
        self._config = config
        self._holder_id = config.holder_id or _default_holder_id()
        self._is_leader = False
        self._running = False
        self._leading_task: Optional[asyncio.Task] = None

    @property
    def is_leader(self) -> bool:
        """True if this instance currently holds the leader lock."""
        return self._is_leader

    @property
    def holder_id(self) -> str:
        """The unique identifier for this instance."""
        return self._holder_id

    async def run(
        self,
        on_started_leading: Callable[[], Coroutine],
        on_stopped_leading: Callable[[], Coroutine],
    ) -> None:
        """Compete for leadership and manage the leading lifecycle.

        Runs until stop() is called. When leadership is gained,
        on_started_leading() is started as a task. When leadership is
        lost, the task is cancelled and on_stopped_leading() is awaited.
        """
        self._running = True
        logger.info(
            "Starting leader election (holder_id=%s, lock=%s)",
            self._holder_id,
            self._config.lock_name,
        )

        while self._running:
            try:
                acquired = await self._db.acquire_or_renew_lock(
                    resource_key=self._config.lock_name,
                    holder_id=self._holder_id,
                    lease_duration_seconds=self._config.lease_duration_seconds,
                )
            except Exception as exc:
                logger.error("Leader election lock error: %s", exc)
                if self._is_leader:
                    # Treat a DB error during renewal as a lost lease
                    await self._stop_leading(on_stopped_leading)
                await asyncio.sleep(self._config.retry_interval_seconds)
                continue

            if acquired:
                if not self._is_leader:
                    # Newly became leader
                    self._is_leader = True
                    logger.info("Became leader (holder_id=%s)", self._holder_id)
                    self._leading_task = asyncio.create_task(on_started_leading())
                # Renew interval — we still hold the lock
                await asyncio.sleep(self._config.renew_interval_seconds)
            else:
                if self._is_leader:
                    # Lost leadership
                    await self._stop_leading(on_stopped_leading)
                else:
                    logger.debug(
                        "Not leader, retrying in %ds",
                        self._config.retry_interval_seconds,
                    )
                await asyncio.sleep(self._config.retry_interval_seconds)

    async def stop(self) -> None:
        """Stop competing for leadership and release the lock if held."""
        self._running = False
        if self._is_leader:
            self._is_leader = False
            if self._leading_task and not self._leading_task.done():
                self._leading_task.cancel()
                try:
                    await self._leading_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await self._db.release_lock(
                    resource_key=self._config.lock_name,
                    holder_id=self._holder_id,
                )
                logger.info("Released leader lock (holder_id=%s)", self._holder_id)
            except Exception as exc:
                logger.error("Failed to release leader lock: %s", exc)

    async def _stop_leading(self, on_stopped_leading: Callable[[], Coroutine]) -> None:
        """Cancel the leading task and invoke the stopped-leading callback."""
        self._is_leader = False
        logger.warning("Lost leadership (holder_id=%s)", self._holder_id)
        if self._leading_task and not self._leading_task.done():
            self._leading_task.cancel()
            try:
                await self._leading_task
            except (asyncio.CancelledError, Exception):
                pass
        self._leading_task = None
        try:
            await on_stopped_leading()
        except Exception as exc:
            logger.error("on_stopped_leading callback error: %s", exc)
