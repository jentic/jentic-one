"""Public testing seam: compliance base classes for extension points.

A downstream package inherits these in its own test suite to prove its
``SearchStrategy`` / ``Broker`` / ``UnregisteredUrlHandler`` implementations
match the built-in contract —
both the method *names* (``runtime_checkable`` ``isinstance``) and the concrete
*signatures* (``inspect.signature``), which a bare Protocol never checks.

``SourceContract`` does the same for a catalog source kind, with the test-only
``static`` kind (``register_static_source_kind``) and ``synthetic_catalog`` as
fixtures. The catalog source interface is provisional.

Shipped in the wheel so ``import jentic_one.testing`` works for downstream repos.
"""

from jentic_one.testing.catalog_source import (
    FakeSourceHttp,
    SourceContract,
    StaticEntry,
    fake_source_deps,
    register_static_source_kind,
    static_source_config,
    synthetic_catalog,
)
from jentic_one.testing.compliance import (
    BaseBrokerComplianceTest,
    BaseSearchStrategyComplianceTest,
    BaseUnregisteredUrlHandlerComplianceTest,
    assert_signature_matches,
)

__all__ = [
    "BaseBrokerComplianceTest",
    "BaseSearchStrategyComplianceTest",
    "BaseUnregisteredUrlHandlerComplianceTest",
    "FakeSourceHttp",
    "SourceContract",
    "StaticEntry",
    "assert_signature_matches",
    "fake_source_deps",
    "register_static_source_kind",
    "static_source_config",
    "synthetic_catalog",
]
