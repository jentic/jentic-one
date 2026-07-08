"""Signature-checking compliance bases for the extension-point seams.

``runtime_checkable`` Protocols validate method *presence* only — an
implementation can drift in parameter names, defaults, or return type and still
pass ``isinstance``. These bases close that gap: each subclass points at its
implementation and inherits tests that assert both the ``isinstance`` conformance
*and* the exact ``inspect.signature`` of every seam method.

Base classes are named ``Base*`` (not ``Test*``) so pytest does not collect them
directly; a subclass named ``Test*`` in the consuming test suite is what runs.
"""

from __future__ import annotations

import inspect

from jentic_one.registry.repos.search.protocol import SearchStrategy
from jentic_one.shared.broker.broker import Broker


def assert_signature_matches(impl: type, protocol: type, method: str) -> None:
    """Assert ``impl.method`` has the same signature as ``protocol.method``.

    Closes the ``runtime_checkable`` gap (which only checks method presence).
    Ignores ``self`` and compares parameter names, kinds, defaults, and
    annotations, plus the return annotation.
    """
    proto_sig = inspect.signature(getattr(protocol, method))
    impl_sig = inspect.signature(getattr(impl, method))
    proto_params = list(proto_sig.parameters.values())[1:]  # drop self
    impl_params = list(impl_sig.parameters.values())[1:]
    assert impl_params == proto_params, (
        f"{impl.__name__}.{method} parameters diverge from "
        f"{protocol.__name__}.{method}: {impl_sig} != {proto_sig}"
    )
    assert impl_sig.return_annotation == proto_sig.return_annotation, (
        f"{impl.__name__}.{method} return type diverges from "
        f"{protocol.__name__}.{method}: {impl_sig.return_annotation!r} != "
        f"{proto_sig.return_annotation!r}"
    )


class BaseSearchStrategyComplianceTest:
    """Subclass and set ``strategy_cls`` to prove a ``SearchStrategy`` conforms.

    The subclass sets ``strategy_cls`` to the zero-arg-constructible strategy
    class, e.g.::

        class TestMyStrategyCompliance(BaseSearchStrategyComplianceTest):
            strategy_cls = MyStrategy
    """

    #: The strategy class under test. Subclasses override this.
    strategy_cls: type

    def test_is_search_strategy(self) -> None:
        assert isinstance(self.strategy_cls(), SearchStrategy)

    def test_has_required_attrs(self) -> None:
        # Instantiate first: `name`/`dialect` may be an instance @property (which
        # evaluates to a property object on the class, not a str). Asserting on an
        # instance handles both plain class attrs and properties.
        instance = self.strategy_cls()
        assert isinstance(instance.name, str)
        assert isinstance(instance.dialect, str)

    def test_search_operations_signature(self) -> None:
        assert_signature_matches(self.strategy_cls, SearchStrategy, "search_operations")


class BaseBrokerComplianceTest:
    """Subclass and override ``broker_factory`` to prove a ``Broker`` conforms.

    ``broker_factory`` returns a ready ``Broker`` instance, e.g.::

        class TestMyBrokerCompliance(BaseBrokerComplianceTest):
            def broker_factory(self) -> Broker:
                return MyBroker(...)
    """

    def broker_factory(self) -> Broker:
        raise NotImplementedError("Subclass must override broker_factory()")

    def test_is_broker(self) -> None:
        assert isinstance(self.broker_factory(), Broker)

    def test_execute_signature(self) -> None:
        assert_signature_matches(type(self.broker_factory()), Broker, "execute")
