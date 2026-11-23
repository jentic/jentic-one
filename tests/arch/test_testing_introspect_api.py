"""Architecture gate: ``jentic_one.testing.introspect`` contract and scoping parity.

Pins:
- ``introspect.__all__``, ``INTROSPECTION_SCHEMA``, function signatures, and
  dataclass field names/order for :class:`RouteFact` and :class:`ScopedTable`;
- exact parity between :func:`scoped_tables` and the live scoping dictionaries in
  ``admin``, ``control``, and ``registry`` plus the three parent-scoped child
  tables (14 tables total), including verifying every model path and column attribute;
- smoke coverage of :func:`build_app` and :func:`route_facts` across all
  deployment shapes in ``APP_SHAPES``;
- module-boundary rule that no module under ``src/`` outside
  ``jentic_one/testing/`` imports ``jentic_one.testing.introspect``.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from types import MappingProxyType

import pytest

from jentic_one.admin.scoping.filters import (
    _DELEGATION_PERMISSIONS as _ADMIN_DELEGATION_PERMISSIONS,
)
from jentic_one.admin.scoping.filters import (
    _EXTRA_OWNER_COLUMNS as _ADMIN_EXTRA_OWNER_COLUMNS,
)
from jentic_one.admin.scoping.filters import _ID_MODELS as _ADMIN_ID_MODELS
from jentic_one.admin.scoping.filters import (
    _OWNED_AGENT_ACTOR_MODELS as _ADMIN_OWNED_AGENT_ACTOR_MODELS,
)
from jentic_one.admin.scoping.filters import _OWNER_MODELS as _ADMIN_OWNER_MODELS
from jentic_one.control.scoping.filters import (
    _ACCESS_FILTER_PROVIDERS as _CONTROL_ACCESS_FILTER_PROVIDERS,
)
from jentic_one.control.scoping.filters import (
    _DELEGATION_PERMISSIONS as _CONTROL_DELEGATION_PERMISSIONS,
)
from jentic_one.control.scoping.filters import (
    _OWNED_AGENT_MODELS as _CONTROL_OWNED_AGENT_MODELS,
)
from jentic_one.control.scoping.filters import _OWNER_MODELS as _CONTROL_OWNER_MODELS
from jentic_one.registry.scoping.filters import _OWNER_MODELS as _REGISTRY_OWNER_MODELS
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.testing import introspect as introspect_module
from jentic_one.testing.introspect import (
    INTROSPECTION_SCHEMA,
    RouteFact,
    ScopedTable,
    access_filter_providers,
    build_app,
    route_facts,
    scoped_tables,
)

from .conftest import SRC_ROOT, python_files_in
from .test_route_auth_gate import APP_SHAPES

pytestmark = pytest.mark.arch

EXPECTED_ALL = [
    "INTROSPECTION_SCHEMA",
    "RouteFact",
    "ScopedTable",
    "access_filter_providers",
    "build_app",
    "route_facts",
    "scoped_tables",
]

EXPECTED_ROUTE_FACT_FIELDS = (
    "method",
    "path",
    "operation_id",
    "module",
    "authenticated",
    "permissions",
    "actor_types",
    "in_schema",
    "auth_via",
    "opaque",
)

EXPECTED_SCOPED_TABLE_FIELDS = (
    "surface",
    "table",
    "model",
    "owner_columns",
    "id_column",
    "delegation_permission",
    "owned_agent_axis",
    "parent",
)


def test_introspect_all_and_schema_constant() -> None:
    """``introspect.__all__`` and ``INTROSPECTION_SCHEMA`` match the contract."""
    assert list(introspect_module.__all__) == EXPECTED_ALL
    for name in EXPECTED_ALL:
        assert hasattr(introspect_module, name), f"Missing export: {name}"
    assert INTROSPECTION_SCHEMA == "jentic.introspect/v1"


def test_introspect_dataclass_fields() -> None:
    """``RouteFact`` and ``ScopedTable`` are frozen kw-only dataclasses with pinned field order."""
    assert tuple(f.name for f in dataclasses.fields(RouteFact)) == EXPECTED_ROUTE_FACT_FIELDS
    assert tuple(f.name for f in dataclasses.fields(ScopedTable)) == EXPECTED_SCOPED_TABLE_FIELDS
    assert RouteFact.__dataclass_params__.frozen is True  # type: ignore[attr-defined]
    assert ScopedTable.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_introspect_function_signatures() -> None:
    """Public functions on ``jentic_one.testing.introspect`` keep their parameter signatures."""
    build_sig = inspect.signature(build_app)
    assert [(p.name, p.kind, p.default) for p in build_sig.parameters.values()] == [
        ("apps", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("config", inspect.Parameter.KEYWORD_ONLY, None),
        ("spa_stub", inspect.Parameter.KEYWORD_ONLY, True),
    ]

    facts_sig = inspect.signature(route_facts)
    assert [(p.name, p.kind) for p in facts_sig.parameters.values()] == [
        ("app", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ]

    tables_sig = inspect.signature(scoped_tables)
    assert list(tables_sig.parameters) == []

    providers_sig = inspect.signature(access_filter_providers)
    assert list(providers_sig.parameters) == []


def test_scoped_tables_parity_with_scoping_filters_and_children() -> None:
    """``scoped_tables()`` reflects every surface's scoping dicts plus the 3 child tables."""
    tables = scoped_tables()
    assert isinstance(tables, MappingProxyType)
    assert len(tables) == 14

    expected: dict[str, ScopedTable] = {}
    for model, col in _ADMIN_OWNER_MODELS.items():
        key = f"admin.{model.__tablename__}"
        expected[key] = ScopedTable(
            surface="admin",
            table=model.__tablename__,
            model=f"{model.__module__}.{model.__qualname__}",
            owner_columns=(
                col.key,
                *(extra.key for extra in _ADMIN_EXTRA_OWNER_COLUMNS.get(model, ())),
            ),
            id_column=_ADMIN_ID_MODELS[model].key if model in _ADMIN_ID_MODELS else None,
            delegation_permission=_ADMIN_DELEGATION_PERMISSIONS.get(model),
            owned_agent_axis=model in _ADMIN_OWNED_AGENT_ACTOR_MODELS,
            parent=None,
        )

    for model, col in _CONTROL_OWNER_MODELS.items():
        key = f"control.{model.__tablename__}"
        expected[key] = ScopedTable(
            surface="control",
            table=model.__tablename__,
            model=f"{model.__module__}.{model.__qualname__}",
            owner_columns=(col.key,),
            id_column=None,
            delegation_permission=_CONTROL_DELEGATION_PERMISSIONS.get(model),
            owned_agent_axis=model in _CONTROL_OWNED_AGENT_MODELS,
            parent=None,
        )

    for model, col in _REGISTRY_OWNER_MODELS.items():
        key = f"registry.{model.__tablename__}"
        expected[key] = ScopedTable(
            surface="registry",
            table=model.__tablename__,
            model=f"{model.__module__}.{model.__qualname__}",
            owner_columns=(col.key,),
            id_column=None,
            delegation_permission=None,
            owned_agent_axis=False,
            parent=None,
        )

    expected["admin.agent_credential_bindings"] = ScopedTable(
        surface="admin",
        table="agent_credential_bindings",
        model="jentic_one.admin.core.schema.agent_credential_bindings.AgentCredentialBinding",
        owner_columns=("agent_id", "credential_id"),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="agent",
    )
    expected["control.agent_permission_rules"] = ScopedTable(
        surface="control",
        table="agent_permission_rules",
        model="jentic_one.control.core.schema.agent_permission_rules.AgentPermissionRule",
        owner_columns=("agent_id", "credential_id"),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="agent",
    )
    expected["admin.job_results"] = ScopedTable(
        surface="admin",
        table="job_results",
        model="jentic_one.admin.core.schema.job_results.JobResult",
        owner_columns=("job_id",),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="job",
    )

    assert dict(tables) == expected

    # Verify every model path and column attribute resolves on the real ORM class.
    for key, entry in tables.items():
        mod_path, cls_name = entry.model.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        assert cls.__tablename__ == entry.table, f"{key}: __tablename__ mismatch"
        for col_name in entry.owner_columns:
            assert hasattr(cls, col_name), f"{key}: missing owner column {col_name!r} on {cls}"
        if entry.id_column is not None:
            assert hasattr(cls, entry.id_column), f"{key}: missing id_column {entry.id_column!r}"


def test_access_filter_providers_returns_tuple_snapshot() -> None:
    """``access_filter_providers()`` returns an immutable tuple matching the control list."""
    providers = access_filter_providers()
    assert isinstance(providers, tuple)
    assert providers == tuple(_CONTROL_ACCESS_FILTER_PROVIDERS)


def test_build_app_and_route_facts_smoke_for_all_shapes() -> None:
    """``build_app`` and ``route_facts`` work for every deployment shape in ``APP_SHAPES``."""
    for shape, apps in APP_SHAPES.items():
        app = build_app(apps)
        facts = route_facts(app)
        assert isinstance(facts, tuple)
        assert facts, f"No route facts returned for shape={shape!r}"

        # Deterministic ordering and no HEAD/OPTIONS or duplicate (method, path).
        keys = [(f.path, f.method) for f in facts]
        assert keys == sorted(keys), f"Non-deterministic order for shape={shape!r}"
        assert len(set(keys)) == len(keys), f"Duplicate (method, path) for shape={shape!r}"
        assert not any(f.method in {"HEAD", "OPTIONS"} for f in facts)

        if shape == "broker":
            proxy_facts = [f for f in facts if f.path == "/{upstream_url}"]
            assert proxy_facts, "Broker proxy route missing from route_facts"
            for pf in proxy_facts:
                assert pf.authenticated is True
                assert pf.auth_via == "broker identity dependency"
                assert pf.permissions == frozenset({BROKER_EXECUTE_PERMISSION})
                assert pf.actor_types == frozenset({"agent"})
        elif shape == "combined":
            authed = [f for f in facts if f.authenticated and not f.opaque]
            assert len(authed) >= 50
            opaque = [f for f in facts if f.opaque]
            assert any(f.path == "/openapi.json" for f in opaque)


def test_no_production_module_outside_testing_imports_introspect() -> None:
    """No module under ``src/`` outside ``jentic_one/testing/`` may import ``introspect``."""
    testing_dir = SRC_ROOT / "testing"
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(testing_dir):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "jentic_one.testing.introspect" or alias.name.startswith(
                        "jentic_one.testing.introspect."
                    ):
                        violations.append(f"{py_file}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "jentic_one.testing.introspect" or node.module.startswith(
                    "jentic_one.testing.introspect."
                ):
                    violations.append(f"{py_file}:{node.lineno} imports from {node.module}")
                elif node.module == "jentic_one.testing":
                    for alias in node.names:
                        if alias.name == "introspect":
                            violations.append(
                                f"{py_file}:{node.lineno} imports introspect "
                                "from jentic_one.testing"
                            )
    assert not violations, (
        "Production modules outside jentic_one/testing/ must not import "
        "jentic_one.testing.introspect:\n" + "\n".join(violations)
    )
