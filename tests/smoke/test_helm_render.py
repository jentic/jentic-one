"""Render-only assertions for the Helm chart (no cluster required).

These run inside the smoke matrix (tools.deploy ci-smoke builds the chart
dependencies before invoking pytest), but they only shell out to
``helm template`` — they skip cleanly when helm or the built dependencies
are absent, so a bare local ``pytest tests/smoke`` stays green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "jentic-one"
VALUES_DIR = CHART_DIR.parent / "values"


def _helm_template(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    if not list((CHART_DIR / "charts").glob("postgresql-*.tgz")):
        pytest.skip("chart dependencies not built (run: helm dependency build)")
    return subprocess.run(
        ["helm", "template", "jentic", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.smoke
def test_render_requires_db_passwords() -> None:
    """The chart ships no password defaults; a bare render must fail loudly."""
    result = _helm_template()
    assert result.returncode != 0
    assert "password is required" in result.stderr


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_dev_values(values: str) -> None:
    """The committed dev values files carry their own (dev-only) passwords."""
    result = _helm_template("-f", str(VALUES_DIR / f"{values}.yaml"))
    assert result.returncode == 0, result.stderr
