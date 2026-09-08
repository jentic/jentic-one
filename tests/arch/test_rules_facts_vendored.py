"""Guard the vendored rules-facts fallback: OSS-safety and upstream drift.

Two independent guards protect the committed ``tests/arch/vendored/orm.facts.yaml``:

1. ``test_vendored_orm_facts_is_oss_safe`` (ALWAYS runs): the vendored file may
   contain ONLY facts that are meaningful in this public repo. It fails if an
   unexpected top-level section, an unknown ORM base, or a table name that does
   not exist in this repo's models sneaks in — so a re-vendor can never leak
   content that only makes sense in a downstream (non-public) repo.

2. ``test_vendored_orm_facts_matches_mounted_source`` (runs only when the
   external rules repo is mounted): ensures the vendored copy has not drifted
   from the upstream source of truth.
"""

from __future__ import annotations

import ast

import pytest
import yaml

from ._rules_facts import VENDORED_DIR, mounted_facts_path, orm_facts
from .conftest import SRC_ROOT, python_files_in

# Top-level keys the vendored ORM facts file is allowed to carry. Anything else
# (e.g. a downstream-only section) must not appear in the public repo.
_ALLOWED_FACT_KEYS = frozenset(
    {
        "schema_version",
        "primary_key",
        "valid_bases",
        "audit_mixins",
        "tablename",
        "required_columns",
        "ksuid_exempt_tables",
    }
)


def _oss_declarative_bases() -> frozenset[str]:
    """Return the ORM declarative base class names defined in this repo."""
    base_file = SRC_ROOT / "shared" / "db" / "base.py"
    tree = ast.parse(base_file.read_text(encoding="utf-8"))
    bases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Base"):
            bases.add(node.name)
    return frozenset(bases)


def _oss_tablenames() -> frozenset[str]:
    """Return every ``__tablename__`` string literal declared in this repo's models."""
    names: set[str] = set()
    for py_file in python_files_in(SRC_ROOT):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__tablename__"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    names.add(node.value.value)
    return frozenset(names)


@pytest.mark.arch
def test_vendored_orm_facts_is_oss_safe() -> None:
    """The vendored facts must reference only symbols that exist in this repo.

    This is the primary leak guard: it turns "the vendored file must not carry
    downstream-only content" from a review promise into an enforced test that
    runs in this repo's CI, with or without the external rules repo mounted.
    """
    facts = orm_facts()

    unexpected_keys = set(facts) - _ALLOWED_FACT_KEYS
    assert not unexpected_keys, (
        f"Vendored orm.facts.yaml has unexpected top-level keys {sorted(unexpected_keys)}. "
        f"Only {sorted(_ALLOWED_FACT_KEYS)} are allowed in the public repo — re-vendor the "
        "OSS-applicable subset only."
    )

    oss_bases = _oss_declarative_bases()
    unknown_bases = set(facts.get("valid_bases", [])) - oss_bases
    assert not unknown_bases, (
        f"Vendored orm.facts.yaml lists valid_bases {sorted(unknown_bases)} that are not "
        f"defined in this repo (known: {sorted(oss_bases)}). A base that only exists downstream "
        "must not appear in the public vendored facts."
    )

    oss_tables = _oss_tablenames()
    unknown_tables = set(facts.get("ksuid_exempt_tables", [])) - oss_tables
    assert not unknown_tables, (
        f"Vendored orm.facts.yaml exempts tables {sorted(unknown_tables)} that have no "
        "__tablename__ in this repo's models. A table that only exists downstream must not "
        "appear in the public vendored facts."
    )


@pytest.mark.arch
def test_vendored_orm_facts_matches_mounted_source() -> None:
    mounted = mounted_facts_path("orm.facts.yaml")
    if mounted is None:
        pytest.skip("rules repo not mounted; vendored copy is authoritative")
    vendored = VENDORED_DIR / "orm.facts.yaml"
    assert yaml.safe_load(vendored.read_text(encoding="utf-8")) == yaml.safe_load(
        mounted.read_text(encoding="utf-8")
    ), (
        "Vendored orm.facts.yaml drifted from the mounted rules repo. "
        "Re-vendor: copy the mounted file over tests/arch/vendored/orm.facts.yaml."
    )
