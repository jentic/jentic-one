"""Public testing seam: compliance base classes for extension points.

A downstream package inherits these in its own test suite to prove its
``SearchStrategy`` / ``Broker`` implementations match the built-in contract —
both the method *names* (``runtime_checkable`` ``isinstance``) and the concrete
*signatures* (``inspect.signature``), which a bare Protocol never checks.

Shipped in the wheel so ``import jentic_one.testing`` works for downstream repos.
"""

from jentic_one.testing.compliance import (
    BaseBrokerComplianceTest,
    BaseSearchStrategyComplianceTest,
    assert_signature_matches,
)

__all__ = [
    "BaseBrokerComplianceTest",
    "BaseSearchStrategyComplianceTest",
    "assert_signature_matches",
]
