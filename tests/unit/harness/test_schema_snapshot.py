"""Unit tests for ``tests.harness.schema_snapshot``."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    text,
)

from tests.harness.schema_snapshot import _schema_snapshot, schema_snapshot


def test_schema_snapshot_detects_extra_index(tmp_path: Path) -> None:
    """A database with an extra index differs from the same database without it."""
    base_db = tmp_path / "base.db"
    indexed_db = tmp_path / "indexed.db"

    for db_file, with_extra_index in ((base_db, False), (indexed_db, True)):
        engine = create_engine(f"sqlite:///{db_file}")
        try:
            meta = MetaData()
            widgets = Table(
                "widgets",
                meta,
                Column("id", String(30), primary_key=True),
                Column("name", String(100), nullable=False),
            )
            if with_extra_index:
                Index("ix_widgets_name", widgets.c.name)
            meta.create_all(engine)
        finally:
            engine.dispose()

    snap_without = _schema_snapshot(base_db)
    snap_with = _schema_snapshot(indexed_db)

    assert snap_without != snap_with
    assert snap_without["widgets"]["indexes"] == []
    assert snap_with["widgets"]["indexes"] == [("ix_widgets_name", ("name",), False)]


def test_schema_snapshot_captures_columns_constraints_fks_and_excludes_alembic_version(
    tmp_path: Path,
) -> None:
    """Snapshot reflects columns, unique constraints, and FKs while ignoring alembic_version."""
    db_file = tmp_path / "full.db"
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        meta = MetaData()
        Table(
            "parents",
            meta,
            Column("id", String(30), primary_key=True),
            Column("slug", String(64), nullable=False),
            UniqueConstraint("slug", name="uq_parents_slug"),
        )
        Table(
            "children",
            meta,
            Column("id", String(30), primary_key=True),
            Column(
                "parent_id",
                String(30),
                ForeignKey("parents.id", name="fk_children_parent_id"),
                nullable=False,
            ),
            Column("note", String(255), nullable=True),
            Column("rank", Integer(), nullable=False),
        )
        meta.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('abc123')"))

        snap = schema_snapshot(engine)
    finally:
        engine.dispose()

    assert "alembic_version" not in snap
    assert set(snap) == {"parents", "children"}
    assert snap["parents"]["columns"] == [
        ("id", "VARCHAR(30)", False),
        ("slug", "VARCHAR(64)", False),
    ]
    assert snap["parents"]["unique_constraints"] == [("uq_parents_slug", ("slug",))]
    assert snap["children"]["foreign_keys"] == [
        ("fk_children_parent_id", ("parent_id",), "parents", ("id",))
    ]
