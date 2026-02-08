"""Unit tests for migrate.py - Database migration runner."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import migrate
from migrate import (
    discover_migrations,
    ensure_migration_table,
    get_applied_versions,
    apply_migration,
    run_migrations,
)


class TestDiscoverMigrations:
    """Tests for discover_migrations function."""

    def test_returns_sorted_list(self, tmp_path, monkeypatch):
        """Test migrations are discovered and sorted by version."""
        (tmp_path / "002_add_column.sql").write_text(
            "ALTER TABLE resources ADD col TEXT;"
        )
        (tmp_path / "001_initial.sql").write_text("CREATE TABLE test (id INT);")
        (tmp_path / "003_add_index.sql").write_text("CREATE INDEX idx ON test(id);")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert len(result) == 3
        assert result[0][0] == "001"
        assert result[0][1] == "001_initial.sql"
        assert result[1][0] == "002"
        assert result[2][0] == "003"

    def test_skips_non_sql_files(self, tmp_path, monkeypatch):
        """Test that non-SQL files are ignored."""
        (tmp_path / "001_initial.sql").write_text("CREATE TABLE test (id INT);")
        (tmp_path / "002_readme.txt").write_text("not a migration")
        (tmp_path / "003_script.py").write_text("print('hello')")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert len(result) == 1
        assert result[0][0] == "001"

    def test_skips_non_matching_names(self, tmp_path, monkeypatch):
        """Test that files without NNN_ prefix are ignored."""
        (tmp_path / "001_valid.sql").write_text("SELECT 1;")
        (tmp_path / "schema.sql").write_text("SELECT 1;")
        (tmp_path / "abc_description.sql").write_text("SELECT 1;")
        (tmp_path / "1_too_short.sql").write_text("SELECT 1;")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert len(result) == 1
        assert result[0][0] == "001"

    def test_empty_directory(self, tmp_path, monkeypatch):
        """Test empty directory returns empty list."""
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert result == []

    def test_missing_directory(self, tmp_path, monkeypatch):
        """Test missing directory raises FileNotFoundError."""
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path / "nonexistent")

        with pytest.raises(FileNotFoundError):
            discover_migrations()

    def test_skips_directories(self, tmp_path, monkeypatch):
        """Test that subdirectories matching the pattern are ignored."""
        (tmp_path / "001_initial.sql").write_text("SELECT 1;")
        (tmp_path / "002_subdir.sql").mkdir()
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert len(result) == 1
        assert result[0][0] == "001"

    def test_returns_full_path(self, tmp_path, monkeypatch):
        """Test that returned paths are correct."""
        sql_file = tmp_path / "001_initial.sql"
        sql_file.write_text("SELECT 1;")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        result = discover_migrations()

        assert result[0][2] == sql_file


@pytest.mark.asyncio
class TestEnsureMigrationTable:
    """Tests for ensure_migration_table function."""

    async def test_executes_create_table(self):
        """Test that CREATE TABLE is executed."""
        conn = AsyncMock()

        await ensure_migration_table(conn)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS schema_migrations" in sql
        assert "version" in sql
        assert "filename" in sql
        assert "applied_at" in sql


@pytest.mark.asyncio
class TestGetAppliedVersions:
    """Tests for get_applied_versions function."""

    async def test_returns_version_set(self):
        """Test that applied versions are returned as a set."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"version": "001"}, {"version": "002"}])

        result = await get_applied_versions(conn)

        assert result == {"001", "002"}

    async def test_returns_empty_set(self):
        """Test empty table returns empty set."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        result = await get_applied_versions(conn)

        assert result == set()


@pytest.mark.asyncio
class TestApplyMigration:
    """Tests for apply_migration function."""

    async def test_executes_sql_and_records(self, tmp_path):
        """Test that migration SQL is executed and recorded."""
        sql_file = tmp_path / "001_initial.sql"
        sql_file.write_text("CREATE TABLE test (id INT);")

        conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=mock_transaction)

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        await apply_migration(pool, "001", "001_initial.sql", sql_file)

        assert conn.execute.call_count == 2
        conn.execute.assert_any_call("CREATE TABLE test (id INT);")
        conn.execute.assert_any_call(
            "INSERT INTO schema_migrations (version, filename) " "VALUES ($1, $2)",
            "001",
            "001_initial.sql",
        )

    async def test_propagates_exception(self, tmp_path):
        """Test that SQL errors propagate."""
        sql_file = tmp_path / "001_bad.sql"
        sql_file.write_text("INVALID SQL;")

        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=Exception("syntax error"))
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=mock_transaction)

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        with pytest.raises(Exception, match="syntax error"):
            await apply_migration(pool, "001", "001_bad.sql", sql_file)


@pytest.mark.asyncio
class TestRunMigrations:
    """Tests for run_migrations function."""

    async def test_applies_pending_migrations(self, tmp_path, monkeypatch):
        """Test that only pending migrations are applied."""
        (tmp_path / "001_initial.sql").write_text("CREATE TABLE t1 (id INT);")
        (tmp_path / "002_update.sql").write_text("ALTER TABLE t1 ADD col TEXT;")
        (tmp_path / "003_index.sql").write_text("CREATE INDEX idx ON t1(id);")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"version": "001"}])

        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=mock_transaction)

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        result = await run_migrations(pool)

        assert result == 2

    async def test_fresh_database(self, tmp_path, monkeypatch):
        """Test applying migrations on a fresh database."""
        (tmp_path / "001_initial.sql").write_text("CREATE TABLE t1 (id INT);")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        mock_transaction = AsyncMock()
        mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
        mock_transaction.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=mock_transaction)

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        result = await run_migrations(pool)

        assert result == 1

    async def test_no_pending_migrations(self, tmp_path, monkeypatch):
        """Test when all migrations are already applied."""
        (tmp_path / "001_initial.sql").write_text("CREATE TABLE t1 (id INT);")
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"version": "001"}])

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        result = await run_migrations(pool)

        assert result == 0

    async def test_no_migration_files(self, tmp_path, monkeypatch):
        """Test with empty migrations directory."""
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            yield conn

        pool.acquire = mock_acquire

        result = await run_migrations(pool)

        assert result == 0

    async def test_ensures_migration_table_first(self, tmp_path, monkeypatch):
        """Test that schema_migrations table is created before anything else."""
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

        conn = AsyncMock()
        pool = AsyncMock()

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        await run_migrations(pool)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS schema_migrations" in sql
