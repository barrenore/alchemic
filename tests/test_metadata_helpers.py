"""Unit tests for the shared metadata helpers in ``alchemic._metadata``."""

from typing import Annotated

from alchemic._metadata import extract_metadata_items, normalize_meta_dict
from alchemic.extensions import Extension, FieldExtension


def test_extract_metadata_items_none():
    assert extract_metadata_items(None) == []


def test_extract_metadata_items_plain_annotated():
    items = extract_metadata_items(Annotated[str, Extension(editable=False), "tag"])
    assert len(items) == 2


def test_extract_metadata_items_wrapped_in_container():
    # Mapped[Annotated[str, ...]] -- the form the declared annotation takes.
    from sqlalchemy.orm import Mapped

    annotation = Mapped[Annotated[int, Extension(file_field=True)]]
    items = extract_metadata_items(annotation)
    assert items == [Extension(file_field=True)]


def test_extract_metadata_items_non_annotated_container():
    # A container whose own first arg is not Annotated yields nothing.
    assert extract_metadata_items(int | None) == []
    assert extract_metadata_items(list[int]) == []


def test_normalize_meta_dict_plain_mapping():
    assert normalize_meta_dict({"a": 1}) == {"a": 1}


def test_normalize_meta_dict_as_dict_object():
    assert normalize_meta_dict(Extension(editable=False)) == {"editable": False}


def test_normalize_meta_dict_non_dict_as_dict_return_none():
    class Weird:
        def as_dict(self):
            return "not-a-dict"

    assert normalize_meta_dict(Weird()) is None


def test_normalize_meta_dict_no_as_dict_returns_none():
    assert normalize_meta_dict(42) is None


def test_normalize_meta_dict_field_extension():
    fe = FieldExtension(custom_tag="media")
    assert normalize_meta_dict(fe) == {"custom_tag": "media"}
