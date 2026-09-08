"""Tests for the multi-database migration infrastructure."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
from sqlalchemy import MetaData

from jentic_one.migrations.registry.versions import (
    e6f7a8b9c0d1_normalize_url_index_path_templates as url_index_repair_migration,
)
from jentic_one.migrations.targets import (
    DB_METADATA,
    DB_TARGETS,
    MigrationTarget,
    register_target,
)
from jentic_one.registry.core import url_index as live_url_index
from jentic_one.shared.db.base import AdminBase, ControlBase, RegistryBase


def test_registry_base_has_own_metadata() -> None:
    assert isinstance(RegistryBase.metadata, MetaData)


def test_control_base_has_own_metadata() -> None:
    assert isinstance(ControlBase.metadata, MetaData)


def test_admin_base_has_own_metadata() -> None:
    assert isinstance(AdminBase.metadata, MetaData)


def test_bases_have_distinct_metadata() -> None:
    metadatas = {
        id(RegistryBase.metadata),
        id(ControlBase.metadata),
        id(AdminBase.metadata),
    }
    assert len(metadatas) == 3


def test_resolve_registry() -> None:
    assert "registry" in DB_METADATA
    assert DB_METADATA["registry"] is RegistryBase.metadata


def test_resolve_control() -> None:
    assert "control" in DB_METADATA
    assert DB_METADATA["control"] is ControlBase.metadata


def test_resolve_admin() -> None:
    assert "admin" in DB_METADATA
    assert DB_METADATA["admin"] is AdminBase.metadata


def test_all_databases_covered() -> None:
    assert set(DB_METADATA.keys()) == {"registry", "control", "admin"}


def test_db_targets_cover_oss_surfaces() -> None:
    assert set(DB_TARGETS.keys()) == {"registry", "control", "admin"}


def test_db_targets_carry_metadata_and_default_version_table() -> None:
    for name, base in (
        ("registry", RegistryBase),
        ("control", ControlBase),
        ("admin", AdminBase),
    ):
        target = DB_TARGETS[name]
        assert target.metadata is base.metadata
        assert target.version_table == "alembic_version"


def test_db_metadata_shim_matches_targets() -> None:
    assert {name: t.metadata for name, t in DB_TARGETS.items()} == DB_METADATA


def test_register_target_is_idempotent_for_same_target() -> None:
    # Re-registering an identical target is a no-op (safe for repeat imports).
    register_target(DB_TARGETS["registry"])
    assert set(DB_TARGETS.keys()) == {"registry", "control", "admin"}


def test_register_target_rejects_conflicting_redefinition() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_target(MigrationTarget("registry", ControlBase.metadata))


@pytest.mark.parametrize("db_name", ["registry", "control", "admin"])
def test_versions_directory_exists(db_name: str) -> None:
    versions_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "jentic_one"
        / "migrations"
        / db_name
        / "versions"
    )
    assert versions_dir.is_dir(), f"Missing versions directory for {db_name}"


@pytest.mark.parametrize("db_name", ["registry", "control", "admin"])
def test_script_template_exists(db_name: str) -> None:
    template = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "jentic_one"
        / "migrations"
        / db_name
        / "script.py.mako"
    )
    assert template.is_file(), f"Missing script.py.mako for {db_name}"


@pytest.fixture()
def ini_config():
    ini_path = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    config = configparser.ConfigParser()
    config.read(ini_path)
    return config


@pytest.mark.parametrize("section", ["registry", "control", "admin"])
def test_alembic_section_exists(ini_config, section: str) -> None:
    assert ini_config.has_section(section), f"Missing [{section}] in alembic.ini"


@pytest.mark.parametrize("section", ["registry", "control", "admin"])
def test_alembic_section_has_script_location(ini_config, section: str) -> None:
    assert ini_config.has_option(section, "script_location")
    location = ini_config.get(section, "script_location")
    assert Path(location).is_dir()
    assert (Path(location) / "env.py").is_file()


def test_admin_migration_seeds_no_credentials() -> None:
    """The admin migration must NOT seed any user, secret, or grant.

    The platform moved to a no-credential first run: the first admin is created
    at runtime via ``POST /users:create-admin`` (AuthService.bootstrap_admin),
    not seeded by the schema migration. A reintroduced seed (e.g. a copy-pasted
    ``admin@local`` / ``1234`` block) would resurrect default credentials and
    silently defeat ``setup_required``. Guard the migration source against any
    ``op.bulk_insert`` / ``INSERT`` so that can't sneak back in.
    """
    versions = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "jentic_one"
        / "migrations"
        / "admin"
        / "versions"
    )
    for name in (
        "c2d3e4f5a6b7_add_users_secrets_invites.py",
        "d3e4f5a6b7c8_add_user_permission_grants.py",
        "w2x3y4z5a6b7_add_setup_sentinel.py",
    ):
        source = (versions / name).read_text()
        lowered = source.lower()
        assert "admin@local" not in lowered, f"{name} re-seeds the default admin account"
        assert "bulk_insert" not in lowered, f"{name} inserts seed rows; first run must stay empty"
        # A row seed reaches the DB via either op.bulk_insert (above) or raw SQL
        # (op.execute("INSERT INTO ...")). Check the raw-SQL path directly rather
        # than the broad "values(" substring, which false-positives on benign
        # server_default / comment text.
        assert "insert into" not in lowered, f"{name} inserts seed rows; first run must stay empty"


# Canary corpus for the URL-index repair migration: representative templates
# covering trailing slashes, parameters, RFC 6570 operators, percent-encoding,
# dot segments, and the root path.
_URL_INDEX_CANARY_TEMPLATES = [
    "/api/bootstrap-static/",
    "/api/v1/",
    "/v1/pets",
    "/pets/{petId}",
    "/users/{id}/",
    "/users/{id}/posts/{postId}/",
    "/files/{+path}",
    "/files/{+path}/",
    "/v1beta/{+property}:runReport",
    "/foo%20bar/{id}",
    "/a/b/../c/{id}/",
    "/",
]


@pytest.mark.parametrize("template", _URL_INDEX_CANARY_TEMPLATES)
def test_url_index_repair_migration_matches_live_normalization(template: str) -> None:
    """The e6f7a8b9c0d1 data migration's frozen helpers must agree with the
    live ``registry.core.url_index`` functions.

    The migration deliberately inlines frozen copies instead of importing the
    live module, so a later refactor can't silently rewrite what the
    historical migration did. If this test fails, normalization semantics
    changed: do NOT edit the frozen copies — write a NEW data migration that
    re-canonicalizes existing ``operation_url_indexes`` rows.
    """
    mig = url_index_repair_migration
    live = live_url_index

    canonical = live.normalize_path_template(template)
    assert mig._normalize_path_template(template) == canonical
    assert mig._build_path_regex_pattern(canonical) == live.build_path_regex(canonical).pattern
    assert mig._count_segments(canonical) == live.count_segments(canonical)
