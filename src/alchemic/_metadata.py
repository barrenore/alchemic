"""Read the ``Annotated`` metadata of a field's declared annotation."""

from collections.abc import Callable, Mapping
from typing import Annotated, Any, cast, get_args, get_origin

__all__ = ["as_dict", "extract_metadata_items", "normalize_meta_dict"]


def as_dict(value: object) -> dict[str, Any] | None:
    """Return a ``dict`` copy of ``value`` if it is a ``Mapping``, else ``None``."""
    return dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else None


def extract_metadata_items(annotation: Any) -> list[Any]:
    """Return the ``Annotated`` extras of ``annotation``, or of an ``Annotated`` one level inside it.

    ``Annotated[T, x]`` and ``Mapped[Annotated[T, x]]`` both give ``[x]``.
    """
    if annotation is None:
        return []

    if get_origin(annotation) is Annotated:
        return list(get_args(annotation)[1:])

    if get_origin(annotation) is not None:
        for arg in get_args(annotation):
            if get_origin(arg) is Annotated:
                return list(get_args(arg)[1:])

    return []


def normalize_meta_dict(item: Any) -> dict[str, Any] | None:
    """Return ``item`` as a dict if it is a ``Mapping`` or its ``as_dict()`` returns one, else ``None``."""
    mapping = as_dict(item)
    if mapping is not None:
        return mapping
    method = _get_as_dict(item)
    return None if method is None else as_dict(method())


def _get_as_dict(item: Any) -> Callable[[], Any] | None:
    """Return ``item.as_dict`` if present and callable, else ``None``."""
    method = getattr(item, "as_dict", None)
    return method if callable(method) else None
