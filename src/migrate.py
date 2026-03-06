"""
Database migration runner for asyncpg.

Applies forward-only SQL migrations from the migrations/ directory.
Each migration runs in its own transaction for atomicity.
"""

import logging
import re
from pathlib import Path
from typing import List, Set, Tuple

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

MIGRATION_PATTERN = re.compile(r"^(\d{3})_.+\.sql$")


async def ensure_migration_table(conn: asyncpg.Connection) -> None:
    """Create the schema_migrations tracking table if it doesn't exist."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id SERIAL PRIMARY KEY,
            version VARCHAR(255) NOT NULL UNIQUE,
            filename VARCHAR(255) NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """)


def discover_migrations() -> List[Tuple[str, str, Path]]:
    """
    Discover migration files in the migrations directory.

    Returns:
        Sorted list of (version, filename, path) tuples.

    Raises:
        FileNotFoundError: If the migrations directory doesn't exist.
    """
    if not MIGRATIONS_DIR.is_dir():
        raise FileNotFoundError(f"Migrations directory not found: {MIGRATIONS_DIR}")

    migrations = []
    for entry in sorted(MIGRATIONS_DIR.iterdir()):
        match = MIGRATION_PATTERN.match(entry.name)
        if match and entry.is_file():
            version = match.group(1)
            migrations.append((version, entry.name, entry))

    return migrations


async def get_applied_versions(conn: asyncpg.Connection) -> Set[str]:
    """Get the set of already-applied migration versions."""
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def apply_migration(
    pool: asyncpg.Pool, version: str, filename: str, path: Path
) -> None:
    """
    Apply a single migration in its own transaction.

    Args:
        pool: asyncpg connection pool.
        version: Migration version string (e.g. "001").
        filename: Migration filename for audit trail.
        path: Full path to the SQL file.
    """
    sql = path.read_text(encoding="utf-8")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, filename) " "VALUES ($1, $2)",
                version,
                filename,
            )

    logger.info(f"Applied migration {filename}")


MIGRATION_LOCK_KEY = 8472910346  # Arbitrary stable key for pg_advisory_lock


async def run_migrations(pool: asyncpg.Pool) -> int:
    """
    Discover and apply all pending migrations in order.

    Uses a PostgreSQL advisory lock so that concurrent instances do not race
    to apply the same migrations when starting simultaneously.

    Args:
        pool: An asyncpg connection pool (must already be connected).

    Returns:
        Number of migrations applied.

    Raises:
        FileNotFoundError: If the migrations directory is missing.
        asyncpg.PostgresError: If a migration fails (it is rolled back;
            previously applied migrations remain).
    """
    all_migrations = discover_migrations()
    if not all_migrations:
        logger.info("No migration files found")
        return 0

    async with pool.acquire() as conn:
        # Acquire a session-level advisory lock — blocks until the lock is free.
        # This ensures only one instance runs migrations at a time.
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_KEY)
        try:
            await ensure_migration_table(conn)
            # Re-read applied versions *inside* the lock so we see any
            # migrations already applied by another instance that held the
            # lock before us.
            applied = await get_applied_versions(conn)
            pending = [(v, name, path) for v, name, path in all_migrations if v not in applied]

            if not pending:
                logger.info("Database schema is up to date")
                return 0

            logger.info(f"Applying {len(pending)} pending migration(s)")

            for version, filename, path in pending:
                await apply_migration(pool, version, filename, path)

            logger.info(f"Successfully applied {len(pending)} migration(s)")
            return len(pending)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY)
