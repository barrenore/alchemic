"""SQLAlchemy's private names and attributes, for the rest of alchemic to use.

``tests/test_private_api.py`` pins their behaviour, and the ``<2.1`` pin covers them.
"""

# pyright: reportPrivateUsage=false
# pylint: disable=protected-access

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.orm import MappedColumn, Mapper, MapperProperty, RelationshipProperty
from sqlalchemy.orm.base import _MappedAnnotationBase
from sqlalchemy.sql.base import _NoArg

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio.session import _AsyncSessionBind
    from sqlalchemy.orm.interfaces import _AttributeOptions
    from sqlalchemy.orm.relationships import _RelationshipArg, _RelationshipArgs
    from sqlalchemy.sql._typing import _AutoIncrementType, _InfoType
    from sqlalchemy.sql.schema import _ServerDefaultArgument, _ServerOnUpdateArgument
    from sqlalchemy.sql.type_api import _BindProcessorType

__all__ = [
    "NO_ARG",
    "AsyncSessionBind",
    "AutoIncrementType",
    "BindProcessorType",
    "InfoType",
    "NoArg",
    "ServerDefaultArgument",
    "ServerOnUpdateArgument",
    "attribute_options",
    "exclude_from_init",
    "is_mapped_annotation",
    "is_populated",
    "mapper_props",
    "relationship_args",
]

type AsyncSessionBind = _AsyncSessionBind
type AutoIncrementType = _AutoIncrementType
type BindProcessorType[T] = _BindProcessorType[T]
type InfoType = _InfoType
type NoArg = _NoArg
type ServerDefaultArgument = _ServerDefaultArgument
type ServerOnUpdateArgument = _ServerOnUpdateArgument

NO_ARG: Final = _NoArg.NO_ARG


def attribute_options(column: MappedColumn[Any]) -> "_AttributeOptions":
    """The dataclass options ``column`` was declared with: ``init``, ``default`` and the rest."""
    return column._attribute_options


def exclude_from_init(column: MappedColumn[Any]) -> None:
    """Record ``init=False`` on ``column`` where SQLAlchemy's dataclass mapping would."""
    column._attribute_options = column._attribute_options._replace(dataclasses_init=False)


def is_mapped_annotation(cls: type) -> bool:
    """Whether ``cls`` is ``Mapped``, ``WriteOnlyMapped``, ``DynamicMapped`` or a subclass."""
    return issubclass(cls, _MappedAnnotationBase)


def mapper_props(mapper: Mapper[Any]) -> Mapping[str, MapperProperty[Any]]:
    """``mapper``'s properties, read without configuring it."""
    return mapper._props


def relationship_args(rel: RelationshipProperty[Any]) -> "_RelationshipArgs":
    """The arguments ``rel`` was declared with, settable until mappers are configured."""
    return rel._init_args


def is_populated(arg: "_RelationshipArg[Any, Any]") -> bool:
    """Whether the relationship argument ``arg`` was given."""
    return arg._is_populated()
