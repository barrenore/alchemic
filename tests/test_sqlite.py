"""``configure_sqlite``: PRAGMAs on connect, the BEGIN mode of each transaction, and refused row locks.

File-backed databases, since WAL and locking need a real file. Each test uses its own ``tmp_path``.
"""

import threading
import time

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import CompileError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import sessionmaker

from alchemic import Mapped, Model, Session, async_sessionmaker, configure_sqlite, mapped_column


class Ticket(Model):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str] = mapped_column(sa.String(40), default="untitled")


def _record_statements(engine: sa.Engine | AsyncEngine) -> list[str]:
    statements: list[str] = []
    target = engine.sync_engine if isinstance(engine, AsyncEngine) else engine

    @event.listens_for(target, "before_cursor_execute")
    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    return statements


def _insert_ticket(engine: sa.Engine) -> None:
    with Session(engine) as session:
        session.add(Ticket(title="a"))
        session.commit()


async def _insert_ticket_async(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        session.add(Ticket(title="a"))
        await session.commit()


@pytest.fixture
def file_engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    configure_sqlite(engine)
    Model.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def no_wait_engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'locks.db'}")
    configure_sqlite(engine, busy_timeout_ms=0)
    Model.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
async def async_file_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'async.db'}")
    configure_sqlite(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize(
    ("options", "foreign_keys", "busy_timeout", "journal_mode"),
    [
        ({}, 1, 5000, "wal"),
        ({"foreign_keys": False, "busy_timeout_ms": 1234, "journal_mode": "DELETE"}, 0, 1234, "delete"),
        ({"journal_mode": None}, 1, 5000, "delete"),
    ],
)
def test_pragmas_are_applied_on_connect(tmp_path, options, foreign_keys, busy_timeout, journal_mode):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pragmas.db'}")
    configure_sqlite(engine, **options)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == foreign_keys
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == busy_timeout
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == journal_mode
    engine.dispose()


def test_foreign_keys_are_enforced(file_engine):
    with file_engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE fk_child (parent_id INTEGER REFERENCES fk_parent (id))")
    with pytest.raises(IntegrityError), file_engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO fk_child (parent_id) VALUES (99)")


def test_a_transaction_begins_deferred_by_default(file_engine):
    statements = _record_statements(file_engine)
    with file_engine.connect() as conn:
        conn.execute(sa.select(Ticket)).all()
    assert "BEGIN DEFERRED" in statements


@pytest.mark.parametrize("mode", ["DEFERRED", "IMMEDIATE", "EXCLUSIVE"])
def test_begin_mode_from_an_engine_with_execution_options(file_engine, mode):
    statements = _record_statements(file_engine)
    maker = sessionmaker(bind=file_engine.execution_options(alchemic_sqlite_begin=mode), class_=Session)
    with maker() as session:
        session.execute(sa.select(Ticket)).all()
    assert f"BEGIN {mode}" in statements


def test_begin_mode_from_the_first_call_on_a_session(file_engine):
    statements = _record_statements(file_engine)
    with Session(file_engine) as session:
        session.connection(execution_options={"alchemic_sqlite_begin": "IMMEDIATE"})
        session.add(Ticket(title="first"))
        session.commit()
    assert "BEGIN IMMEDIATE" in statements


def test_begin_mode_from_a_connection_with_execution_options(file_engine):
    statements = _record_statements(file_engine)
    with file_engine.connect().execution_options(alchemic_sqlite_begin="EXCLUSIVE") as conn:
        conn.begin()
        conn.execute(sa.select(Ticket)).all()
    assert "BEGIN EXCLUSIVE" in statements


def test_begin_immediate_blocks_another_writer_before_any_write(no_wait_engine):
    holder = no_wait_engine.connect().execution_options(alchemic_sqlite_begin="IMMEDIATE")
    other = no_wait_engine.connect()
    try:
        holder.begin()
        other.begin()
        with pytest.raises(OperationalError, match="database is locked"):
            other.execute(sa.insert(Ticket).values(title="blocked"))
    finally:
        other.rollback()
        holder.rollback()
        other.close()
        holder.close()


def test_begin_deferred_does_not_block_another_writer_before_it_writes(no_wait_engine):
    holder = no_wait_engine.connect()
    other = no_wait_engine.connect()
    try:
        holder.begin()
        other.begin()
        other.execute(sa.insert(Ticket).values(title="allowed"))
        other.commit()
    finally:
        holder.rollback()
        holder.close()
        other.close()
    with no_wait_engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(Ticket.__table__)) == 1


@pytest.mark.parametrize("mode", ["LOCKED", "immediate", ""])
def test_an_unknown_begin_mode_raises(file_engine, mode):
    with (
        pytest.raises(ValueError, match="alchemic_sqlite_begin must be one of DEFERRED"),
        file_engine.connect().execution_options(alchemic_sqlite_begin=mode) as conn,
    ):
        conn.begin()


def test_an_unknown_begin_mode_raises_from_a_session(file_engine):
    maker = sessionmaker(bind=file_engine.execution_options(alchemic_sqlite_begin="LOCKED"), class_=Session)
    with maker() as session, pytest.raises(ValueError, match="alchemic_sqlite_begin"):
        session.execute(sa.select(Ticket)).all()


def test_configuring_twice_still_begins_once(file_engine):
    configure_sqlite(file_engine)
    statements = _record_statements(file_engine)
    with file_engine.connect() as conn:
        conn.execute(sa.select(Ticket)).all()
    assert [s for s in statements if s.startswith("BEGIN")] == ["BEGIN DEFERRED"]


def test_bad_settings_are_rejected(file_engine):
    with pytest.raises(ValueError, match="journal_mode"):
        configure_sqlite(file_engine, journal_mode="WAL; DROP TABLE support_tickets")
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        configure_sqlite(file_engine, busy_timeout_ms=-1)


def test_a_non_sqlite_engine_is_rejected():
    engine = sa.create_engine("postgresql+psycopg://alchemic:alchemic@localhost:5443/alchemic_test")
    with pytest.raises(ValueError, match="needs a sqlite engine, not 'postgresql'"):
        configure_sqlite(engine)
    engine.dispose()


def test_configuring_again_replaces_the_settings(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'again.db'}")
    configure_sqlite(engine, busy_timeout_ms=100)
    configure_sqlite(engine, foreign_keys=False, busy_timeout_ms=200, journal_mode=None)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 200
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
    engine.dispose()


def test_configuring_one_engine_leaves_another_alone(tmp_path):
    configured = sa.create_engine(f"sqlite:///{tmp_path / 'configured.db'}")
    plain = sa.create_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    configure_sqlite(configured)
    with plain.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        assert conn.execute(sa.select(sa.literal(1)).with_for_update()).scalar_one() == 1
    configured.dispose()
    plain.dispose()


def test_a_rolled_back_transaction_leaves_no_rows(file_engine):
    with pytest.raises(RuntimeError), Session(file_engine) as session:
        session.add(Ticket(title="discarded"))
        session.flush()
        raise RuntimeError
    with Session(file_engine) as session:
        assert session.scalars(sa.select(Ticket)).all() == []


def test_ddl_is_transactional(file_engine):
    with file_engine.connect() as conn:
        conn.begin()
        conn.exec_driver_sql("CREATE TABLE scratch (id INTEGER)")
        conn.rollback()
    assert "scratch" not in sa.inspect(file_engine).get_table_names()


def test_a_savepoint_rolls_back_alone(file_engine):
    with Session(file_engine) as session:
        session.add(Ticket(title="kept"))
        with session.begin_nested() as savepoint:
            session.add(Ticket(title="undone"))
            session.flush()
            savepoint.rollback()
        session.commit()
    with Session(file_engine) as session:
        assert session.scalars(sa.select(Ticket.title)).all() == ["kept"]


def test_a_deferred_read_then_write_fails_without_waiting(file_engine):
    _insert_ticket(file_engine)
    reader = file_engine.connect()
    try:
        reader.begin()
        reader.execute(sa.select(Ticket)).all()
        _insert_ticket(file_engine)
        started = time.monotonic()
        with pytest.raises(OperationalError, match="database is locked"):
            reader.execute(sa.insert(Ticket).values(title="late"))
        assert time.monotonic() - started < 1
    finally:
        reader.rollback()
        reader.close()


def test_an_immediate_transaction_waits_for_the_writer_ahead_of_it(file_engine):
    holding = threading.Event()

    def hold_the_write_lock() -> None:
        with file_engine.connect().execution_options(alchemic_sqlite_begin="IMMEDIATE") as conn:
            conn.begin()
            conn.execute(sa.insert(Ticket).values(title="first"))
            holding.set()
            time.sleep(0.3)
            conn.commit()

    holder = threading.Thread(target=hold_the_write_lock)
    holder.start()
    try:
        assert holding.wait(5)
        started = time.monotonic()
        with file_engine.connect().execution_options(alchemic_sqlite_begin="IMMEDIATE") as conn:
            conn.begin()
            assert time.monotonic() - started >= 0.2
            conn.execute(sa.insert(Ticket).values(title="second"))
            conn.commit()
    finally:
        holder.join()
    with file_engine.connect() as conn:
        assert conn.scalars(sa.select(Ticket.title).order_by(Ticket.id)).all() == ["first", "second"]


def test_the_refusal_names_the_begin_option(file_engine):
    with (
        file_engine.connect() as conn,
        pytest.raises(CompileError, match="alchemic_sqlite_begin='IMMEDIATE'"),
    ):
        conn.execute(_locking_select())


def _locking_select() -> sa.Select[tuple[Ticket]]:
    return sa.select(Ticket).where(Ticket.id == 1).with_for_update()


def _refresh_locked(session: Session) -> None:
    session.refresh(session.get(Ticket, 1), with_for_update=True)


SYNC_LOCK_PATHS = {
    "session.execute": lambda session: session.execute(_locking_select()),
    "session.scalars": lambda session: session.scalars(_locking_select()),
    "session.exec": lambda session: session.exec(_locking_select()),
    "session.get": lambda session: session.get(Ticket, 1, with_for_update=True),
    "session.refresh": _refresh_locked,
    "session.query": lambda session: session.query(Ticket).with_for_update().all(),
}


@pytest.mark.parametrize("path", list(SYNC_LOCK_PATHS))
def test_for_update_is_refused_on_every_session_path(file_engine, path):
    _insert_ticket(file_engine)
    with Session(file_engine) as session:
        with pytest.raises(CompileError, match="no row locks"):
            SYNC_LOCK_PATHS[path](session)
        assert session.scalars(sa.select(Ticket.title)).one() == "a"


def test_for_share_is_refused(file_engine):
    _insert_ticket(file_engine)
    with Session(file_engine) as session, pytest.raises(CompileError, match="no row locks"):
        session.execute(sa.select(Ticket).with_for_update(read=True))


def test_for_update_is_refused_on_a_connection(file_engine):
    with file_engine.connect() as conn, pytest.raises(CompileError, match="no row locks"):
        conn.execute(_locking_select())


async def _refresh_locked_async(session) -> None:
    await session.refresh(await session.get(Ticket, 1), with_for_update=True)


ASYNC_LOCK_PATHS = {
    "session.execute": lambda session: session.execute(_locking_select()),
    "session.scalars": lambda session: session.scalars(_locking_select()),
    "session.exec": lambda session: session.exec(_locking_select()),
    "session.get": lambda session: session.get(Ticket, 1, with_for_update=True),
    "session.refresh": _refresh_locked_async,
    "session.stream_exec": lambda session: session.stream_exec(_locking_select()),
}


@pytest.mark.parametrize("path", list(ASYNC_LOCK_PATHS))
async def test_for_update_is_refused_on_every_async_session_path(async_file_engine, path):
    await _insert_ticket_async(async_file_engine)
    async with async_sessionmaker(async_file_engine)() as session:
        with pytest.raises(CompileError, match="no row locks"):
            await ASYNC_LOCK_PATHS[path](session)
        assert (await session.scalars(sa.select(Ticket.title))).one() == "a"


async def test_for_update_is_refused_on_an_async_connection(async_file_engine):
    async with async_file_engine.connect() as conn:
        with pytest.raises(CompileError, match="no row locks"):
            await conn.execute(_locking_select())


async def test_pragmas_apply_to_async_connections(async_file_engine):
    async with async_file_engine.connect() as conn:
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 1
        assert (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == 5000
        assert (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar_one() == "wal"


async def test_async_transactions_begin_deferred_by_default(async_file_engine):
    statements = _record_statements(async_file_engine)
    async with async_file_engine.connect() as conn:
        await conn.execute(sa.select(Ticket))
    assert "BEGIN DEFERRED" in statements


async def test_begin_mode_from_the_first_call_on_an_async_session(async_file_engine):
    statements = _record_statements(async_file_engine)
    async with async_sessionmaker(async_file_engine)() as session:
        await session.connection(execution_options={"alchemic_sqlite_begin": "IMMEDIATE"})
        session.add(Ticket(title="first"))
        await session.commit()
    assert "BEGIN IMMEDIATE" in statements


async def test_begin_mode_from_an_async_engine_with_execution_options(async_file_engine):
    statements = _record_statements(async_file_engine)
    writer = async_file_engine.execution_options(alchemic_sqlite_begin="IMMEDIATE")
    async with async_sessionmaker(writer)() as session:
        session.add(Ticket(title="first"))
        await session.commit()
    assert "BEGIN IMMEDIATE" in statements


async def test_async_savepoint_rolls_back_alone(async_file_engine):
    async with async_sessionmaker(async_file_engine)() as session:
        session.add(Ticket(title="kept"))
        savepoint = await session.begin_nested()
        session.add(Ticket(title="undone"))
        await session.flush()
        await savepoint.rollback()
        await session.commit()
        assert (await session.scalars(sa.select(Ticket.title))).all() == ["kept"]
