"""Configure a SQLite engine: foreign keys, busy timeout, WAL, explicit BEGIN modes, and no row locks."""

from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.asyncio import AsyncEngine

from alchemic._sqlalchemy_private import for_update_arg

__all__ = ["configure_sqlite"]

_BEGIN_MODES: Final = ("DEFERRED", "IMMEDIATE", "EXCLUSIVE")
_BEGIN_OPTION: Final = "alchemic_sqlite_begin"
_JOURNAL_MODES: Final = frozenset({"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"})


def _begin(conn: Connection) -> None:
    mode = conn.get_execution_options().get(_BEGIN_OPTION, "DEFERRED")
    if mode not in _BEGIN_MODES:
        raise ValueError(f"{_BEGIN_OPTION} must be one of {', '.join(_BEGIN_MODES)}, not {mode!r}")
    conn.exec_driver_sql(f"BEGIN {mode}")


def _refuse_row_locks(
    _conn: Connection, statement: object, _multiparams: object, _params: object, _options: object
) -> None:
    if for_update_arg(statement) is not None:
        raise CompileError(
            "SQLite has no row locks, so FOR UPDATE and FOR SHARE are refused. To make a transaction "
            f"wait for other writers, run it with execution option {_BEGIN_OPTION}='IMMEDIATE'."
        )


def configure_sqlite(
    engine: Engine | AsyncEngine,
    *,
    foreign_keys: bool = True,
    busy_timeout_ms: int = 5000,
    journal_mode: str | None = "WAL",
) -> None:
    """Configure every connection ``engine`` opens for SQLite, and take over transaction control.

    On connect, sets ``PRAGMA foreign_keys``, ``PRAGMA busy_timeout`` and, unless
    ``journal_mode`` is ``None``, ``PRAGMA journal_mode``. Each transaction starts with
    ``BEGIN`` in the mode its connection's ``alchemic_sqlite_begin`` execution option names:
    ``"DEFERRED"`` by default, or ``"IMMEDIATE"`` or ``"EXCLUSIVE"``. A statement with
    ``FOR UPDATE`` or ``FOR SHARE`` raises ``CompileError``, since SQLite has no row locks.

    Call before the engine opens its first connection, because the PRAGMAs run on connect.
    Raises ``ValueError`` if the engine's dialect is not ``sqlite``, if ``busy_timeout_ms`` is
    negative, or if ``journal_mode`` is not a SQLite journal mode.
    """
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if sync_engine.dialect.name != "sqlite":
        raise ValueError(f"configure_sqlite needs a sqlite engine, not {sync_engine.dialect.name!r}")
    if busy_timeout_ms < 0:
        raise ValueError(f"busy_timeout_ms must not be negative, not {busy_timeout_ms}")

    statements = [
        f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}",
        f"PRAGMA busy_timeout = {busy_timeout_ms}",
    ]
    if journal_mode is not None:
        if journal_mode.upper() not in _JOURNAL_MODES:
            raise ValueError(f"journal_mode must be a SQLite journal mode, not {journal_mode!r}")
        statements.append(f"PRAGMA journal_mode = {journal_mode.upper()}")

    def _on_connect(dbapi_connection: Any, _record: object) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            for statement in statements:
                cursor.execute(statement)
        finally:
            cursor.close()

    event.listen(sync_engine, "connect", _on_connect)
    if not event.contains(sync_engine, "begin", _begin):
        event.listen(sync_engine, "begin", _begin)
    if not event.contains(sync_engine, "before_execute", _refuse_row_locks):
        event.listen(sync_engine, "before_execute", _refuse_row_locks)
