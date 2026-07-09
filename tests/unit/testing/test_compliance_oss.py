"""Self-test for the ``jentic_one.testing`` compliance harness.

Runs the shipped compliance bases against the built-in default implementations so
CI guarantees the harness itself is correct (and that the built-in SearchStrategy
/ Broker never drift from their seams).

This repo forbids ``class Test*`` in test files (``tests/arch/test_no_test_classes``),
so instead of subclassing the bases with ``Test*`` names (the shape a consuming
test suite uses), we instantiate underscore-prefixed subclasses and drive their
inherited harness methods from plain parametrized functions. This exercises the
exact same harness code paths (``assert_signature_matches`` + ``isinstance``).
"""

from __future__ import annotations

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.default_broker import DefaultBroker
from jentic_one.broker.services.execution.pipeline import BrokerExecutionPipeline
from jentic_one.registry.repos.search.postgres_lexical import PostgresLexicalStrategy
from jentic_one.registry.repos.search.sqlite_lexical import SqliteLexicalStrategy
from jentic_one.shared.broker.broker import Broker
from jentic_one.testing import BaseBrokerComplianceTest, BaseSearchStrategyComplianceTest


class _NoopRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:  # pragma: no cover - unused
        return RunnerResult(status_code=200, body=b"", headers={}, content_type=None, duration_ms=0)


class _SqliteCompliance(BaseSearchStrategyComplianceTest):
    strategy_cls = SqliteLexicalStrategy


class _PostgresCompliance(BaseSearchStrategyComplianceTest):
    strategy_cls = PostgresLexicalStrategy


class _DefaultBrokerCompliance(BaseBrokerComplianceTest):
    def broker_factory(self) -> Broker:
        return DefaultBroker(BrokerExecutionPipeline(_NoopRunner()))


@pytest.mark.parametrize("compliance", [_SqliteCompliance(), _PostgresCompliance()])
def test_oss_search_strategies_comply(compliance: BaseSearchStrategyComplianceTest) -> None:
    compliance.test_is_search_strategy()
    compliance.test_has_required_attrs()
    compliance.test_search_operations_signature()


def test_oss_default_broker_complies() -> None:
    compliance = _DefaultBrokerCompliance()
    compliance.test_is_broker()
    compliance.test_execute_signature()
    compliance.test_execute_streaming_signature()
