from typing import Annotated

from alchemic import (
    Extension,
    FieldExtension,
    Mapped,
    Model,
    get_field_info,
    get_field_metadata,
    mapped_column,
)


class Asset(Model):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(info={"editable": False, "description": "Asset name"})
    file_path: Mapped[Annotated[str, Extension(file_field=True, allow_upload=False)]] = mapped_column()
    tag: Mapped[Annotated[str, FieldExtension(custom_tag="media")]] = mapped_column(default="general")


def test_field_metadata_from_column_info():
    meta = get_field_metadata(Asset, "name")
    assert meta.get("editable") is False
    assert meta.get("description") == "Asset name"

    asset = Asset(id=1, name="Texture", file_path="/path/to/texture.png")
    assert get_field_info(asset, "name")["editable"] is False


def test_field_metadata_from_annotated_extension():
    meta = get_field_metadata(Asset, "file_path")
    assert meta.get("file_field") is True
    assert meta.get("allow_upload") is False

    assert Asset.__table__.c.file_path.info.get("file_field") is True


def test_field_metadata_from_field_extension():
    meta = get_field_metadata(Asset, "tag")
    assert meta.get("custom_tag") == "media"


def test_json_schema_includes_info():
    schema = Asset.model_json_schema()
    props = schema["properties"]
    assert props["name"].get("editable") is False
    assert props["file_path"].get("file_field") is True


def test_extension_as_dict_partial_fields():

    ext = Extension(editable=False)
    assert ext.as_dict() == {"editable": False}


def test_extension_as_dict_with_extra():

    ext = Extension(editable=False, extra={"color": "red"})
    assert ext.as_dict() == {"editable": False, "color": "red"}


def test_extension_as_dict_empty():

    assert Extension().as_dict() == {}


def test_field_ext_kwargs():

    fe = FieldExtension(custom_tag="media", rank=1)
    assert fe.as_dict() == {"custom_tag": "media", "rank": 1}


def test_field_ext_repr():

    fe = FieldExtension(a=1)
    assert "a=1" in repr(fe)


def test_field_ext_eq():

    assert FieldExtension(a=1) == FieldExtension(a=1)
    assert FieldExtension(a=1) != FieldExtension(a=2)
    assert FieldExtension(a=1) != "not-field-ext"


def test_field_ext_hash():

    assert len({FieldExtension(a=1, b=2), FieldExtension(b=2, a=1), FieldExtension(a=9)}) == 2


def test_get_field_info_alias_returns_same():

    assert get_field_info(Asset, "name") == get_field_metadata(Asset, "name")


def test_get_field_metadata_unknown_field():
    assert get_field_metadata(Asset, "does_not_exist") == {}


def test_get_field_metadata_on_instance():
    asset = Asset(id=1, name="x", file_path="/tmp/f.png")
    assert get_field_metadata(asset, "file_path").get("file_field") is True
