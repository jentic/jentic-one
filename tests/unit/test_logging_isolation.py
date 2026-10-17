"""Pins the suite-wide logging isolation in ``tests/harness/logging_isolation.py`` (#1778).

Each scenario runs as its own pytest session in a subprocess, so the outer
session's isolation (which keeps structlog's first-use caching off for the
whole process) cannot mask what the inner session does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog
from structlog import configure as configure_alias
from structlog.testing import capture_logs

pytest_plugins = ("pytester",)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = "tests.harness.logging_isolation"
_INNER_SESSION_TIMEOUT_S = 60

_CONFIG = """
from jentic_one.shared.config import AppConfig

CONFIG = AppConfig.model_validate(
    {
        "databases": {
            "registry": {"name": "r"},
            "admin": {"name": "a"},
            "control": {"name": "c"},
        },
        "runtime": {"log_level": "INFO"},
    }
)
"""

# Two tests run ``configure_logging()`` and log through a module-level proxy,
# the way app modules do; a third asserts ``capture_logs()`` sees that proxy.
_CAPTURE_AFTER_CONFIGURE = (
    _CONFIG
    + """
import structlog
from structlog.testing import capture_logs

from jentic_one.shared.logging import configure_logging

_logger = structlog.get_logger("isolation_probe")


def test_1_configures_logging_and_logs():
    configure_logging(CONFIG)
    _logger.info("first_use")


def test_2_configures_logging_again():
    configure_logging(CONFIG)


def test_3_capture_logs_sees_the_module_proxy():
    with capture_logs() as logs:
        _logger.info("probe")
    assert [e["event"] for e in logs] == ["probe"]
"""
)

# One test changes every piece of global logging state the fixture restores;
# the next asserts it is back to what the first test started with.
_STATE_RESTORED = """
import logging
import warnings

import structlog

_SEEN = {}


def test_1_changes_global_logging_state(tmp_path):
    _SEEN["root_level"] = logging.getLogger().level
    _SEEN["processors"] = structlog.get_config()["processors"]
    handler = logging.FileHandler(tmp_path / "sink.log")
    logging.getLogger().addHandler(handler)
    _SEEN["handler"] = handler
    logging.getLogger().setLevel(logging.CRITICAL)
    fresh = logging.getLogger("created.during.test")
    fresh.setLevel(logging.ERROR)
    fresh.addHandler(logging.NullHandler())
    fresh.propagate = False
    logging.disable(logging.ERROR)
    logging.captureWarnings(True)
    structlog.contextvars.bind_contextvars(leaked="yes")
    structlog.configure(processors=[structlog.processors.KeyValueRenderer()])


def test_2_global_logging_state_is_restored():
    root = logging.getLogger()
    assert root.level == _SEEN["root_level"]
    assert _SEEN["handler"] not in root.handlers
    assert _SEEN["handler"].stream is None
    fresh = logging.getLogger("created.during.test")
    assert (fresh.level, fresh.handlers, fresh.propagate) == (logging.NOTSET, [], True)
    assert logging.root.manager.disable == logging.NOTSET
    assert logging.getLogger("x").isEnabledFor(logging.WARNING)
    assert not getattr(logging, "_warnings_showwarning", None)
    assert structlog.contextvars.get_contextvars() == {}
    assert structlog.get_config()["processors"] is _SEEN["processors"]
    assert not structlog.is_configured()
"""


@pytest.fixture()
def run_inner(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONPATH", str(_REPO_ROOT))

    def run(source: str, *plugin_args: str) -> pytest.RunResult:
        pytester.makepyfile(test_inner=source)
        # Each inner session takes well under a second; the timeout keeps a
        # hung one (e.g. a stuck import) from hanging the outer run.
        return pytester.runpytest_subprocess(
            "-p", "no:cacheprovider", *plugin_args, timeout=_INNER_SESSION_TIMEOUT_S
        )

    return run


def test_capture_logs_sees_module_proxy_after_configure_logging_in_earlier_tests(run_inner) -> None:
    run_inner(_CAPTURE_AFTER_CONFIGURE, "-p", _PLUGIN).assert_outcomes(passed=3)


def test_capture_after_configure_scenario_fails_without_isolation(run_inner) -> None:
    """The scenario above is only a regression pin if it fails without the plugin."""
    run_inner(_CAPTURE_AFTER_CONFIGURE).assert_outcomes(passed=2, failed=1)


def test_global_logging_state_is_restored_after_each_test(run_inner) -> None:
    run_inner(_STATE_RESTORED, "-p", _PLUGIN).assert_outcomes(passed=2)


def test_first_use_caching_stays_off_through_every_configure_entry_point() -> None:
    structlog.reset_defaults()
    structlog.configure_once(cache_logger_on_first_use=True)
    structlog.configure(None, None, None, None, True)
    configure_alias(cache_logger_on_first_use=True)
    assert structlog.get_config()["cache_logger_on_first_use"] is False

    proxy = structlog.get_logger("isolation_probe")
    with capture_logs():
        proxy.info("first_use")
    # A caching proxy replaces its own ``bind`` with the finalized logger's.
    assert "bind" not in vars(proxy)
