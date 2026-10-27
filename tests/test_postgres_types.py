"""PostgreSQL column types: JSONB, pgvector, PostGIS, and models over existing tables."""

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.mutable import MutableDict

from alchemic import Mapped, Session, create_model_base, mapped_column

pytestmark = pytest.mark.postgres


def _require_extension(engine: sa.Engine, name: str) -> None:
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {name}"))
    except DBAPIError:
        pytest.skip(f"Postgres extension {name!r} is not installed")


def test_jsonb_round_trips_and_tracks_replacement(pg_engine):
    base = create_model_base(name="JsonbBase")

    class Document(base):  # type: ignore[valid-type,misc]
        __tablename__ = "pgt_documents"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        body: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
        tags: Mapped[list[str] | None] = mapped_column(JSONB, default=None)

    base.metadata.create_all(pg_engine)
    with Session(pg_engine) as session:
        document = Document(body={"a": 1}, tags=["x"])
        session.add(document)
        session.commit()
        document.body = {"a": 2}
        session.commit()
        session.refresh(document)
        assert document.body == {"a": 2}
        assert document.tags == ["x"]
        assert Document().body == {}


def test_jsonb_in_place_mutation_needs_mutable_dict(pg_engine):
    base = create_model_base(name="MutableBase")

    class Plain(base):  # type: ignore[valid-type,misc]
        __tablename__ = "pgt_plain"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        body: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    class Tracked(base):  # type: ignore[valid-type,misc]
        __tablename__ = "pgt_tracked"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        body: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSONB), default=dict)

    base.metadata.create_all(pg_engine)
    with Session(pg_engine) as session:
        plain = Plain(body={"a": 1})
        tracked = Tracked(body={"a": 1})
        session.add_all([plain, tracked])
        session.commit()
        plain.body["b"] = 2
        tracked.body["b"] = 2
        session.commit()
        session.refresh(plain)
        session.refresh(tracked)
        assert plain.body == {"a": 1}
        assert tracked.body == {"a": 1, "b": 2}


def test_pgvector_column(pg_engine):
    pgvector = pytest.importorskip("pgvector.sqlalchemy")
    _require_extension(pg_engine, "vector")
    base = create_model_base(name="VectorBase")

    class Chunk(base):  # type: ignore[valid-type,misc]
        __tablename__ = "pgt_chunks"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        embedding: Mapped[list[float]] = mapped_column(pgvector.Vector(3))

    base.metadata.create_all(pg_engine)
    with Session(pg_engine) as session:
        session.add_all([Chunk(embedding=[1, 2, 3]), Chunk(embedding=[9, 9, 9])])
        session.commit()
        nearest = session.exec(Chunk.where().order_by(Chunk.embedding.l2_distance([1, 2, 4]))).first()
        assert nearest is not None
        assert list(nearest.embedding) == [1.0, 2.0, 3.0]


def test_postgis_geometry_column(pg_engine):
    geoalchemy2 = pytest.importorskip("geoalchemy2")
    _require_extension(pg_engine, "postgis")
    base = create_model_base(name="GisBase")

    class Place(base):  # type: ignore[valid-type,misc]
        __tablename__ = "pgt_places"
        id: Mapped[int] = mapped_column(primary_key=True, init=False)
        location: Mapped[Any] = mapped_column(geoalchemy2.Geometry("POINT", srid=4326))

    base.metadata.create_all(pg_engine)
    with Session(pg_engine) as session:
        session.add(Place(location="SRID=4326;POINT(1 2)"))
        session.commit()
        nearby = session.exec(
            Place.where(sa.func.ST_DWithin(Place.location, sa.func.ST_GeomFromText("POINT(1 2)", 4326), 1))
        ).all()
        assert len(nearby) == 1
        assert isinstance(nearby[0].location, geoalchemy2.WKBElement)


def test_model_over_reflected_table_declares_columns_by_annotation(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE pgt_legacy (id serial PRIMARY KEY, label text NOT NULL, extra jsonb)")
        )
        connection.execute(sa.text("INSERT INTO pgt_legacy (label, extra) VALUES ('a', '{\"k\": 1}')"))
    base = create_model_base(name="ReflectedBase")
    table = sa.Table("pgt_legacy", base.metadata, autoload_with=pg_engine)

    class Legacy(base):  # type: ignore[valid-type,misc]
        __table__ = table
        id: Mapped[int]
        label: Mapped[str]
        extra: Mapped[dict[str, Any] | None]

    with Session(pg_engine) as session:
        row = session.exec(Legacy.where()).one()
        assert row.model_dump() == {"id": 1, "label": "a", "extra": {"k": 1}}
