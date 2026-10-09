"""``Session`` and ``AsyncSession`` with ``exec``, which shapes a statement's result by its arity."""

from collections.abc import Mapping, Sequence
from typing import Any, overload

from sqlalchemy import CursorResult, Result, ScalarResult
from sqlalchemy.engine import TupleResult
from sqlalchemy.exc import ResourceClosedError
from sqlalchemy.ext.asyncio import AsyncScalarResult, AsyncTupleResult
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.orm import Session as _Session
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.selectable import TypedReturnsRows
from sqlalchemy.util import EMPTY_DICT

from alchemic._sqlalchemy_private import AsyncSessionBind

__all__ = ["AsyncSession", "Session", "async_sessionmaker"]

type _Params = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None
type _Shaped = ScalarResult[Any] | TupleResult[Any] | Result[Any]


def _shape(result: Result[Any]) -> _Shaped:
    """Return ``result`` as scalars for one key, as tuples for several, or as is if it has no rows."""
    try:
        keys = result.keys()
    except ResourceClosedError:
        return result
    if len(keys) == 1:
        return result.scalars()
    return result.tuples()


class Session(_Session):
    """A SQLAlchemy ``Session`` with ``exec``. Everything else is unchanged."""

    @overload
    def exec[T](
        self,
        statement: TypedReturnsRows[tuple[T]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> ScalarResult[T]: ...

    @overload
    def exec[T0, T1, *Ts](
        self,
        statement: TypedReturnsRows[tuple[T0, T1, *Ts]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> TupleResult[tuple[T0, T1, *Ts]]: ...

    @overload
    def exec(
        self,
        statement: UpdateBase,
        params: Mapping[str, Any] | None = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> CursorResult[Any]: ...

    # Bulk write overload with a sequence of parameter mappings.
    @overload
    def exec(
        self,
        statement: UpdateBase,
        params: Sequence[Mapping[str, Any]],
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> Result[Any]: ...

    def exec(
        self,
        statement: TypedReturnsRows[Any] | UpdateBase,
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> _Shaped:
        """Execute ``statement`` and return its result shaped by the statement's arity.

            session.exec(select(Hero)).all()              # Sequence[Hero]
            session.exec(select(Hero.name)).one()         # str
            session.exec(select(Hero, Team)).all()        # Sequence[tuple[Hero, Team]]
            session.exec(update(Hero).returning(Hero.id)) # ScalarResult[int]
            session.exec(delete(Hero).where(...)).rowcount
            session.exec(insert(Hero), [{...}, {...}])    # Result: a bulk write, no rowcount

        One selected entity or column gives a ``ScalarResult``, several a ``TupleResult``.
        DML without ``RETURNING`` gives a ``CursorResult``, or a row-less ``Result`` for a
        list of parameter sets.
        """
        return _shape(
            self.execute(
                statement,
                params,
                execution_options=execution_options or EMPTY_DICT,
                bind_arguments=bind_arguments,
            )
        )


class AsyncSession(_AsyncSession):  # pylint: disable=abstract-method
    """A SQLAlchemy ``AsyncSession`` with ``exec`` and ``stream_exec``, not expiring on commit.

    ``expire_on_commit`` defaults to ``False``. Everything else is unchanged.
    """

    def __init__(
        self, bind: AsyncSessionBind | None = None, *, expire_on_commit: bool = False, **kw: Any
    ) -> None:
        super().__init__(bind, expire_on_commit=expire_on_commit, **kw)

    @overload
    async def exec[T](
        self,
        statement: TypedReturnsRows[tuple[T]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> ScalarResult[T]: ...

    @overload
    async def exec[T0, T1, *Ts](
        self,
        statement: TypedReturnsRows[tuple[T0, T1, *Ts]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> TupleResult[tuple[T0, T1, *Ts]]: ...

    @overload
    async def exec(
        self,
        statement: UpdateBase,
        params: Mapping[str, Any] | None = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> CursorResult[Any]: ...

    # Bulk write overload with a sequence of parameter mappings.
    @overload
    async def exec(
        self,
        statement: UpdateBase,
        params: Sequence[Mapping[str, Any]],
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> Result[Any]: ...

    async def exec(
        self,
        statement: TypedReturnsRows[Any] | UpdateBase,
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> _Shaped:
        """Execute ``statement`` and return its result shaped by arity, as ``Session.exec`` does.

        The result is buffered. For a large result, use ``stream_exec``.
        """
        return _shape(
            await self.execute(
                statement,
                params,
                execution_options=execution_options or EMPTY_DICT,
                bind_arguments=bind_arguments,
            )
        )

    @overload
    async def stream_exec[T](
        self,
        statement: TypedReturnsRows[tuple[T]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> AsyncScalarResult[T]: ...

    @overload
    async def stream_exec[T0, T1, *Ts](
        self,
        statement: TypedReturnsRows[tuple[T0, T1, *Ts]],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> AsyncTupleResult[tuple[T0, T1, *Ts]]: ...

    async def stream_exec(
        self,
        statement: TypedReturnsRows[Any],
        params: _Params = None,
        *,
        execution_options: Mapping[str, Any] | None = None,
        bind_arguments: dict[str, Any] | None = None,
    ) -> AsyncScalarResult[Any] | AsyncTupleResult[Any]:
        """Like ``exec``, but rows arrive as they are iterated, with ``async for``.

            async for hero in await session.stream_exec(select(Hero)):
                ...

        The database cursor stays open until the result is exhausted or closed.
        """
        result = await self.stream(
            statement,
            params,
            execution_options=execution_options or EMPTY_DICT,
            bind_arguments=bind_arguments,
        )
        return result.scalars() if len(result.keys()) == 1 else result.tuples()


def async_sessionmaker(
    bind: AsyncSessionBind | None = None, *, expire_on_commit: bool = False, **kw: Any
) -> _async_sessionmaker[AsyncSession]:
    """SQLAlchemy's ``async_sessionmaker``, making alchemic's ``AsyncSession``.

    ``expire_on_commit`` defaults to ``False``. Other keywords go to SQLAlchemy's unchanged.
    """
    return _async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=expire_on_commit, **kw)
