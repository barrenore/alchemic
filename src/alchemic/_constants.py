"""Attribute names and dictionary keys shared across alchemic's modules."""

from typing import Final

ATTR_MAPPER_ARGS: Final = "__mapper_args__"
SA_VERSION_ID_COL: Final = "version_id_col"

PYDANTIC_FIELDS_SET: Final = "__pydantic_fields_set__"
PYDANTIC_EXTRA: Final = "__pydantic_extra__"
PYDANTIC_PRIVATE: Final = "__pydantic_private__"

ATTR_NON_COLUMNS: Final = "__alchemic_non_columns__"
ATTR_DECLARED_DEFAULTS: Final = "__alchemic_declared_defaults__"
ATTR_NO_INIT: Final = "__alchemic_no_init__"
ATTR_NON_COLUMN_ANNOTATIONS: Final = "__alchemic_non_column_annotations__"
ATTR_READONLY: Final = "__alchemic_readonly__"

INFO_READONLY: Final = "alchemic.readonly"
INFO_VERSION: Final = "alchemic.version"

# A subclass of a mapped class inherits these from the parent's mapper.
INHERITED_MAPPER_ARGS_EXCLUSIONS: Final[frozenset[str]] = frozenset({SA_VERSION_ID_COL})

# ``FieldInfo`` attributes Pydantic drops, with an ``UnsupportedFieldAttributeWarning``,
# from a ``Field()`` nested in ``Annotated``.
FIELD_INFO_PASSTHROUGH_ATTRS: Final[tuple[str, ...]] = (
    "exclude",
    "alias",
    "serialization_alias",
    "validation_alias",
)
