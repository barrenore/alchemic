"""``on_commit`` receivers, and the mapper and session events that record changes and run them."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, overload

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.orm.base import object_state
from sqlalchemy.util.concurrency import await_only, in_greenlet

if TYPE_CHECKING:
    from alchemic._typing import TypedAttribute

__all__ = ["Change", "Deleted", "Inserted", "Updated", "on_commit"]

_PENDING = "alchemic.pending_changes"
_COMMITTED = "alchemic.committed_changes"


@dataclass(frozen=True, slots=True)
class Inserted:
    """The transaction inserted the row."""


@dataclass(frozen=True, slots=True)
class Updated:
    """The transaction updated the row: ``fields`` names the columns and relationships it changed."""

    fields: frozenset[str] = frozenset()

    def has_changed(self, attr: "TypedAttribute[Any]", /) -> bool:
        """Whether ``attr`` is among the changed fields: ``change.has_changed(Task.status)``."""
        return attr.key in self.fields


@dataclass(frozen=True, slots=True)
class Deleted:
    """The transaction deleted the row."""


type Change = Inserted | Updated | Deleted
"""What a committed transaction did to one object.

Flushes merge: insert then update is ``Inserted``, insert then delete is not
reported, and ``Updated.fields`` accumulate.
"""

_KINDS: frozenset[type[Change]] = frozenset((Inserted, Updated, Deleted))

type Receiver[Row] = Callable[[Row, Any], Awaitable[None] | None]


class _Register[M, C](Protocol):
    def __call__[R: Awaitable[None] | None](
        self, receiver: Callable[[M, C], R], /
    ) -> Callable[[M, C], R]: ...


@dataclass(slots=True)
class _Registration:
    model: type
    kinds: frozenset[type[Change]]
    receiver: Receiver[Any]


_registrations: list[_Registration] = []
_by_class: dict[tuple[type, type[Change]], list[Receiver[Any]]] = {}


@overload
def on_commit[M](model: type[M], /) -> _Register[M, Change]: ...
@overload
def on_commit[M, C1: Change](model: type[M], k1: type[C1], /) -> _Register[M, C1]: ...
@overload
def on_commit[M, C1: Change, C2: Change](
    model: type[M], k1: type[C1], k2: type[C2], /
) -> _Register[M, C1 | C2]: ...
@overload
def on_commit[M, C1: Change, C2: Change, C3: Change](
    model: type[M], k1: type[C1], k2: type[C2], k3: type[C3], /
) -> _Register[M, C1 | C2 | C3]: ...
def on_commit(model: type, /, *kinds: Any) -> Any:
    """Run the decorated function for each ``model`` a committed transaction changed.

        @on_commit(ScheduleItem)                       # every kind: change is a Change
        async def broadcast(item: ScheduleItem, change: Change) -> None:
            match change:
                case Inserted(): ...
                case Updated(fields=fields): ...
                case Deleted(): ...

        @on_commit(Task, Updated)                      # change is an Updated
        def reindex(task: Task, change: Updated) -> None: ...

    It is called with the object and its change once the outermost transaction
    has committed, in flush order. It gets only the ``kinds`` given, all three
    if none are, and is type-checked against them and ``model``. A subclass of
    ``model`` counts.

    Any SQLAlchemy session runs receivers; an async receiver needs an
    ``AsyncSession``. A receiver must not use the session for SQL. Under
    ``expire_on_commit=True``, an async receiver reading an expired attribute
    gets ``MissingGreenlet``; alchemic's ``AsyncSession`` does not expire on commit.

    Every receiver runs even if one raises; then ``commit()`` raises an
    ``ExceptionGroup`` of their errors, with the data already committed. Not
    durable: if the process stops before the receivers run, they never do.
    """
    unknown = [kind for kind in kinds if kind not in _KINDS]
    if unknown:
        raise TypeError(f"on_commit() takes Inserted, Updated or Deleted, not {unknown}")
    wanted = frozenset(kinds) or _KINDS

    def register(receiver: Receiver[Any], /) -> Receiver[Any]:
        _registrations.append(_Registration(model, wanted, receiver))
        _by_class.clear()
        return receiver

    return register


def receivers_for(cls: type, kind: type[Change]) -> list[Receiver[Any]]:
    """The receivers for a ``cls`` object's ``kind`` change, in registration order."""
    key = (cls, kind)
    found = _by_class.get(key)
    if found is None:
        found = [
            registration.receiver
            for registration in _registrations
            if kind in registration.kinds and issubclass(cls, registration.model)
        ]
        _by_class[key] = found
    return found


@dataclass(slots=True)
class _Pending:
    target: object
    change: Change


type _Layers = dict[SessionTransaction | None, dict[int, _Pending]]


def _savepoint(transaction: SessionTransaction | None) -> SessionTransaction | None:
    """The savepoint ``transaction`` belongs to, or ``None`` for the outermost transaction."""
    while transaction is not None and not transaction.nested:
        transaction = transaction.parent
    return transaction


def _merge(earlier: Change | None, later: Change) -> Change | None:
    """One object's changes across flushes; ``None`` if it no longer exists outside the transaction."""
    match earlier, later:
        case Inserted(), Deleted():
            return None
        case Inserted(), _:
            return earlier
        case Updated(fields=before), Updated(fields=after):
            return Updated(before | after)
        case _:
            return later


def _record(layer: dict[int, _Pending], target: object, change: Change) -> None:
    key = id(target)
    held = layer.get(key)
    merged = _merge(held.change if held else None, change)
    if merged is None:
        layer.pop(key, None)
    else:
        layer[key] = _Pending(target, merged)


def record(kind: type[Change], target: object) -> None:
    """Record ``target``'s change, from a mapper event, if a receiver wants it."""
    if not receivers_for(type(target), kind):
        return
    state = object_state(target)
    session = state.session
    if session is None:
        return
    change: Change
    if kind is Updated:
        fields = frozenset(attr.key for attr in state.attrs if attr.history.has_changes())
        if not fields:
            return
        change = Updated(fields)
    else:
        change = kind()
    layers: _Layers = session.info.setdefault(_PENDING, {})
    _record(layers.setdefault(_savepoint(session.get_nested_transaction()), {}), target, change)


def _record_insert(_mapper: Any, _connection: Any, target: object) -> None:
    record(Inserted, target)


def _record_update(_mapper: Any, _connection: Any, target: object) -> None:
    record(Updated, target)


def _record_delete(_mapper: Any, _connection: Any, target: object) -> None:
    record(Deleted, target)


def install_change_recording(base: type) -> None:
    """Record the inserts, updates and deletes of ``base``'s mapped subclasses for their receivers."""
    event.listen(base, "after_insert", _record_insert, propagate=True)
    event.listen(base, "after_update", _record_update, propagate=True)
    event.listen(base, "after_delete", _record_delete, propagate=True)


def _after_commit(session: Session) -> None:
    """Fold a committed savepoint into its parent, or set the outermost one's changes aside."""
    layers: _Layers | None = session.info.get(_PENDING)
    if not layers:
        return
    savepoint = session.get_nested_transaction()
    layer = layers.pop(savepoint, None)
    if layer is None:
        return
    if savepoint is None:
        session.info[_COMMITTED] = list(layer.values())
        return
    parent = layers.setdefault(_savepoint(savepoint.parent), {})
    for pending in layer.values():
        _record(parent, pending.target, pending.change)


def _within(transaction: SessionTransaction | None, ancestor: SessionTransaction) -> bool:
    while transaction is not None:
        if transaction is ancestor:
            return True
        transaction = transaction.parent
    return False


def _after_soft_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    """Drop what a rolled-back savepoint, or the whole transaction, recorded."""
    layers: _Layers | None = session.info.get(_PENDING)
    if not layers:
        return
    if previous_transaction.parent is None:
        layers.clear()
    elif previous_transaction.nested:
        for savepoint in [candidate for candidate in layers if _within(candidate, previous_transaction)]:
            del layers[savepoint]


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """Run the receivers once the outermost transaction has committed and closed."""
    if transaction.parent is not None or _PENDING not in session.info:
        return
    del session.info[_PENDING]
    committed: list[_Pending] | None = session.info.pop(_COMMITTED, None)
    if committed:
        _dispatch(committed)


def _dispatch(committed: list[_Pending]) -> None:
    errors: list[Exception] = []
    for pending in committed:
        for receiver in receivers_for(type(pending.target), type(pending.change)):
            try:
                _call(receiver, pending)
            # pylint: disable-next=broad-exception-caught
            except Exception as error:  # noqa: BLE001  # every receiver runs, then all errors are raised
                errors.append(error)
    if errors:
        raise ExceptionGroup("on_commit receivers failed", errors)


def _call(receiver: Receiver[Any], pending: _Pending) -> None:
    result = receiver(pending.target, pending.change)
    if not inspect.isawaitable(result):
        return
    if not in_greenlet():
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError(
            f"on_commit receiver {getattr(receiver, '__qualname__', receiver)!s} is async; "
            "commit through an AsyncSession"
        )
    await_only(result)


event.listen(Session, "after_commit", _after_commit)
event.listen(Session, "after_soft_rollback", _after_soft_rollback)
event.listen(Session, "after_transaction_end", _after_transaction_end)
