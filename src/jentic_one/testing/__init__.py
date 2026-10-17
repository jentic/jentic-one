"""Public testing seam: compliance and contract base classes for extension points.

A downstream package inherits these in its own test suite to prove its
``SearchStrategy`` / ``Broker`` / ``UnregisteredUrlHandler`` implementations
match the built-in contract —
both the method *names* (``runtime_checkable`` ``isinstance``) and the concrete
*signatures* (``inspect.signature``), which a bare Protocol never checks — and
its ``InvalidationBus`` implementation keeps the delivery contract
(``BaseInvalidationBusContract`` drives a live bus).

Shipped in the wheel so ``import jentic_one.testing`` works for downstream repos.
"""

from jentic_one.testing.compliance import (
    BaseBrokerComplianceTest,
    BaseSearchStrategyComplianceTest,
    BaseUnregisteredUrlHandlerComplianceTest,
    assert_signature_matches,
)
from jentic_one.testing.invalidation import BaseInvalidationBusContract

__all__ = [
    "BaseBrokerComplianceTest",
    "BaseInvalidationBusContract",
    "BaseSearchStrategyComplianceTest",
    "BaseUnregisteredUrlHandlerComplianceTest",
    "assert_signature_matches",
]
