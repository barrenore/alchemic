"""alchemic -- SQLAlchemy 2.0 models that are also Pydantic models.

One class, one set of ``Mapped[T]`` annotations, and a type checker that can tell
a column from a value.
"""

from sqlalchemy import delete, insert, select, update

from alchemic._receivers import Change, Deleted, Inserted, Updated, on_commit
from alchemic._typing import (
    Mapped,
    Path,
    Related,
    RelatedAttribute,
    TypedAttribute,
    col,
    link,
    mapped_column,
    path,
    prefetch,
    references,
    relationship,
)
from alchemic.extensions import Extension, FieldExtension, get_field_info, get_field_metadata
from alchemic.model import DeclarativeRoot, Model, ModelMeta, create_model_base
from alchemic.session import AsyncSession, Session, async_sessionmaker

__all__ = [
    "AsyncSession",
    "Change",
    "DeclarativeRoot",
    "Deleted",
    "Extension",
    "FieldExtension",
    "Inserted",
    "Mapped",
    "Model",
    "ModelMeta",
    "Path",
    "Related",
    "RelatedAttribute",
    "Session",
    "TypedAttribute",
    "Updated",
    "async_sessionmaker",
    "col",
    "create_model_base",
    "delete",
    "get_field_info",
    "get_field_metadata",
    "insert",
    "link",
    "mapped_column",
    "on_commit",
    "path",
    "prefetch",
    "references",
    "relationship",
    "select",
    "update",
]

__version__ = "0.1.3"
