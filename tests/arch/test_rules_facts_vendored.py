"""Guard the vendored rules-facts fallback against the mounted rules repo.

The vendored ``tests/arch/vendored/orm.facts.yaml`` is what enforces the ORM
conventions in a standalone clone. When the external rules repo is mounted, this
test ensures the vendored copy has not drifted from the upstream source of
truth.
"""

from __future__ import annotations

import pytest
import yaml

from ._rules_facts import VENDORED_DIR, mounted_facts_path


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
