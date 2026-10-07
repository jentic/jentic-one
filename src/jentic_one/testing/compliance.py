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
import typing
from itertools import zip_longest
from typing import Any

from jentic_one.registry.repos.search.protocol import SearchStrategy
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.web.protocols import UnregisteredUrlHandler

#: Dialect names a backend can report from ``DatabaseBackend.dialect_name``.
#: Keep in sync with the backends in ``jentic_one.shared.db.backends`` — a
#: strategy whose ``dialect`` is not one of these can never be resolved by
#: ``resolve_strategy`` (it keys on ``backend.dialect_name``), so a plausible but
#: wrong value like ``"postgresql"`` would pass a bare ``isinstance(dialect, str)``
#: check yet fail at runtime. This constant makes that class of drift a test failure.
KNOWN_BACKEND_DIALECTS: frozenset[str] = frozenset({"postgres", "sqlite"})


#: (name, kind, default, resolved annotation) — one per parameter after ``self``.
_ParamFact = tuple[str, Any, Any, Any]


def _parameter_facts(func: Any) -> tuple[list[_ParamFact], Any]:
    """Normalise a method into comparable parameter facts + return annotation.

    Annotations are resolved with ``typing.get_type_hints``, so string
    annotations (a module with ``from __future__ import annotations``) and
    spelling variants (``Optional[X]`` vs ``X | None``) compare equal to their
    object forms.
    """
    hints = typing.get_type_hints(func)
    params = list(inspect.signature(func).parameters.values())[1:]  # drop self
    facts: list[_ParamFact] = [
        (p.name, p.kind, p.default, hints.get(p.name, inspect.Parameter.empty)) for p in params
    ]
    return facts, hints.get("return", inspect.Parameter.empty)


def _describe(fact: _ParamFact | None) -> str:
    if fact is None:
        return "<missing>"
    name, kind, default, annotation = fact
    suffix = "" if default is inspect.Parameter.empty else f" = {default!r}"
    note = "" if annotation is not inspect.Parameter.empty else " (unannotated)"
    return f"{name}: {annotation!r}{suffix} [{kind.description}]{note}"


def assert_signature_matches(impl: type, protocol: type, method: str) -> None:
    """Assert ``impl.method`` has the same signature as ``protocol.method``.

    Closes the ``runtime_checkable`` gap (which only checks method presence).
    Ignores ``self`` and compares parameter names, kinds, defaults, and
    *resolved* annotations, plus the return annotation — so the check is
    indifferent to whether either side uses ``from __future__ import
    annotations`` or spells unions as ``Optional[X]`` vs ``X | None``.
    """
    fix_hint = (
        f"fix: copy the signature of {protocol.__name__}.{method} verbatim — same "
        "parameter names, same keyword-only markers, same annotations, same return type."
    )
    proto_params, proto_return = _parameter_facts(getattr(protocol, method))
    impl_params, impl_return = _parameter_facts(getattr(impl, method))
    divergences = [
        f"  impl {_describe(impl_fact)}  !=  protocol {_describe(proto_fact)}"
        for impl_fact, proto_fact in zip_longest(impl_params, proto_params)
        if impl_fact != proto_fact
    ]
    assert not divergences, (
        f"{impl.__name__}.{method} parameters diverge from {protocol.__name__}.{method}:\n"
        + "\n".join(divergences)
        + f"\n{fix_hint}"
    )
    assert impl_return == proto_return, (
        f"{impl.__name__}.{method} return type diverges from "
        f"{protocol.__name__}.{method}: {impl_return!r} != {proto_return!r}\n{fix_hint}"
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

    def test_dialect_is_resolvable(self) -> None:
        # A string `dialect` is not enough: it must match a real backend's
        # `dialect_name` or `resolve_strategy((dialect, mode))` never finds this
        # strategy and raises SearchUnsupportedError at runtime. Catch the
        # plausible-but-wrong value (e.g. "postgresql" vs "postgres") here.
        instance = self.strategy_cls()
        assert instance.dialect in KNOWN_BACKEND_DIALECTS, (
            f"{self.strategy_cls.__name__}.dialect={instance.dialect!r} is not a known "
            f"backend dialect {sorted(KNOWN_BACKEND_DIALECTS)}; resolve_strategy would "
            "never find this strategy."
        )

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

    def test_execute_streaming_signature(self) -> None:
        assert_signature_matches(type(self.broker_factory()), Broker, "execute_streaming")


class BaseUnregisteredUrlHandlerComplianceTest:
    """Subclass and override ``handler_factory`` to prove an
    ``UnregisteredUrlHandler`` conforms.

    ``handler_factory`` returns a ready handler instance, e.g.::

        class TestMyHandlerCompliance(BaseUnregisteredUrlHandlerComplianceTest):
            def handler_factory(self) -> UnregisteredUrlHandler:
                return MyHandler(...)

    The handler must be **class-shaped** (an instance with an ``async def
    __call__``), not a bare ``async def`` — ``assert_signature_matches``
    inspects ``type(handler).__call__``, which a plain function cannot satisfy.
    The ``isinstance`` check alone carries little weight here
    (``runtime_checkable`` on a ``__call__``-only protocol matches every
    callable); the signature and coroutine checks are the real guards.
    """

    def handler_factory(self) -> UnregisteredUrlHandler:
        raise NotImplementedError("Subclass must override handler_factory()")

    def test_is_unregistered_url_handler(self) -> None:
        assert isinstance(self.handler_factory(), UnregisteredUrlHandler)

    def test_call_signature(self) -> None:
        assert_signature_matches(type(self.handler_factory()), UnregisteredUrlHandler, "__call__")

    def test_call_is_coroutine_function(self) -> None:
        # ``isinstance`` and the signature check are identical for a sync and an
        # async ``__call__`` — but the broker router awaits the handler, so a
        # sync one passes compliance and then 500s on its first discovery miss.
        handler = self.handler_factory()
        assert inspect.iscoroutinefunction(inspect.unwrap(type(handler).__call__)), (
            f"{type(handler).__name__}.__call__ must be `async def` — the broker router awaits it."
        )
