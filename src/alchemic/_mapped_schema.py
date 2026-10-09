"""Teach Pydantic to validate SQLAlchemy's ``Mapped[T]`` as ``T``."""

from typing import Any, get_args

from sqlalchemy.orm import Mapped

__all__ = ["install_mapped_schema_hook"]


def _mapped_core_schema(_cls: Any, source: Any, handler: Any) -> Any:
    """Validate ``Mapped[T]`` as ``T``, and a bare ``Mapped`` as ``Any``."""
    args = get_args(source)
    return handler.generate_schema(args[0] if args else Any)


def install_mapped_schema_hook() -> None:
    """Install the hook on ``Mapped``, unless SQLAlchemy already provides one."""
    if hasattr(Mapped, "__get_pydantic_core_schema__"):
        return
    Mapped.__get_pydantic_core_schema__ = classmethod(_mapped_core_schema)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
