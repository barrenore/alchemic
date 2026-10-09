"""Field metadata: ``Extension`` and ``FieldExtension`` to declare it, ``get_field_metadata`` to read it."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alchemic._fields import find_column
from alchemic._metadata import as_dict, extract_metadata_items, normalize_meta_dict

__all__ = ["Extension", "FieldExtension", "get_field_info", "get_field_metadata"]


@dataclass(frozen=True)
class Extension:
    """Field metadata with typed keys, declared in the field's ``Annotated`` type.

        name: Mapped[Annotated[str, Extension(editable=False)]] = mapped_column()

    Keys left ``None`` are omitted from ``as_dict()``; ``extra`` adds free-form ones.
    """

    editable: bool | None = None
    file_field: bool | None = None
    allow_upload: bool | None = None
    extra: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The options that are set, with ``extra`` merged in."""
        result: dict[str, Any] = {}
        if self.editable is not None:
            result["editable"] = self.editable
        if self.file_field is not None:
            result["file_field"] = self.file_field
        if self.allow_upload is not None:
            result["allow_upload"] = self.allow_upload
        if self.extra:
            result.update(self.extra)
        return result


class FieldExtension:
    """Field metadata with arbitrary keys, compared and hashed by its contents."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def as_dict(self) -> dict[str, Any]:
        """The keyword arguments this was created with."""
        return dict(self.__dict__)

    def __repr__(self) -> str:
        items = ", ".join(f"{key}={value!r}" for key, value in self.__dict__.items())
        return f"FieldExtension({items})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FieldExtension):
            return self.__dict__ == other.__dict__
        return False

    def __hash__(self) -> int:
        return hash(tuple(sorted((key, repr(value)) for key, value in self.__dict__.items())))


def _get_column_info(cls: type, field_name: str) -> dict[str, Any] | None:
    """Return the SQLAlchemy ``column.info`` dict for a field, if any."""
    column = find_column(cls, field_name)
    if column is None or not column.info:
        return None
    return dict(column.info)


def _get_pydantic_metadata(cls: type, field_name: str) -> list[Any]:
    """Return the Annotated metadata objects declared on a Pydantic field."""
    fields = getattr(cls, "model_fields", None)
    if not fields or field_name not in fields:
        return []
    field = fields[field_name]
    meta_items = list(getattr(field, "metadata", []))
    meta_items.extend(extract_metadata_items(getattr(field, "annotation", None)))
    return meta_items


def _get_schema_extra(cls: type, field_name: str) -> dict[str, Any]:
    """Return a field's ``json_schema_extra`` when it is a dict."""
    fields = getattr(cls, "model_fields", None)
    if not fields or field_name not in fields:
        return {}
    extra = getattr(fields[field_name], "json_schema_extra", None)
    return as_dict(extra) or {}


def get_field_metadata(cls_or_instance: Any, field_name: str) -> dict[str, Any]:
    """Return the metadata declared for a field of a model class or instance.

    Merged from the column's ``info``, then ``Annotated`` metadata such as
    ``Extension``, then ``json_schema_extra``; a later source wins on a shared key.
    """
    cls = cls_or_instance if isinstance(cls_or_instance, type) else type(cls_or_instance)
    result: dict[str, Any] = {}

    if info := _get_column_info(cls, field_name):
        result.update(info)

    for item in _get_pydantic_metadata(cls, field_name):
        if (plain := normalize_meta_dict(item)) is not None:
            result.update(plain)

    result.update(_get_schema_extra(cls, field_name))

    return result


get_field_info = get_field_metadata
