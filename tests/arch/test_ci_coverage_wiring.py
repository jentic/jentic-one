"""Pin CI coverage collection, combined reporting, and coverage omit lists.

``make test-unit`` runs with a legacy ``[tool.coverage.run].omit`` list in
``pyproject.toml`` that excludes repositories and admin/auth services. To
measure true backend coverage without breaking unit-only PR feedback:

1. ``test-integration`` in ``.github/workflows/ci.yml`` runs pytest with
   ``--cov=jentic_one`` under ``tools/coverage-combined.toml`` and uploads
   per-leg ``.coverage.integration-*`` artifacts.
2. ``test-unit`` uploads its ``.coverage.unit`` artifact.
3. The report-only ``coverage`` job depends on both ``test-unit`` and
   ``test-integration``, combines the artifacts, reports against
   ``tools/coverage-combined.toml``, and is wired into ``ci-status`` as
   non-required.
4. ``tools/coverage-combined.toml`` omits only ``migrations/env.py`` — never
   ``repos`` or ``services``.
5. ``pyproject.toml``'s unit omit list is capped at the legacy 5 globs so it
   can shrink in follow-up hardening but never grow.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from tools.ci_pass_markers import COMMON_INPUTS, load_filters

pytestmark = pytest.mark.arch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
COMBINED_RCFILE = REPO_ROOT / "tools" / "coverage-combined.toml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

_ALLOWED_UNIT_OMIT_GLOBS: frozenset[str] = frozenset(
    {
        "src/jentic_one/migrations/env.py",
        "src/jentic_one/*/repos/*.py",
        "src/jentic_one/admin/services/*.py",
        "src/jentic_one/admin/services/_support/*.py",
        "src/jentic_one/auth/services/*.py",
    }
)


def _load_workflow() -> dict[str, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{WORKFLOW} did not parse to a mapping"
    return doc


def _unexpected_unit_omit_globs(omit_globs: list[str]) -> set[str]:
    return set(omit_globs) - _ALLOWED_UNIT_OMIT_GLOBS


def test_integration_job_collects_coverage() -> None:
    doc = _load_workflow()
    job = doc["jobs"]["test-integration"]
    job_env = job.get("env") or {}
    steps = job.get("steps") or []

    run_steps = [
        step
        for step in steps
        if isinstance(step.get("run"), str) and "make test-integration" in step["run"]
    ]
    assert len(run_steps) == 2, (
        f"Expected 2 integration test run steps (postgres and sqlite), found {len(run_steps)}"
    )

    for step in run_steps:
        step_name = step.get("name", "<unnamed>")
        merged_env = {**job_env, **(step.get("env") or {})}
        pytest_args = str(merged_env.get("PYTEST_ARGS", ""))
        cov_file = str(merged_env.get("COVERAGE_FILE", ""))
        assert "--cov=jentic_one" in pytest_args, (
            f"{WORKFLOW}: step {step_name!r} in test-integration must pass "
            f"--cov=jentic_one in PYTEST_ARGS (got {pytest_args!r})"
        )
        assert cov_file.startswith(".coverage.integration-"), (
            f"{WORKFLOW}: step {step_name!r} in test-integration must set "
            f"COVERAGE_FILE=.coverage.integration-... (got {cov_file!r})"
        )

    upload_steps = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert upload_steps, (
        f"{WORKFLOW}: test-integration must upload .coverage.* via actions/upload-artifact"
    )


def test_coverage_job_combines_unit_and_integration() -> None:
    doc = _load_workflow()
    jobs = doc["jobs"]
    assert "coverage" in jobs, f"{WORKFLOW}: missing 'coverage' job"

    cov_job = jobs["coverage"]
    needs = cov_job.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    assert {"test-unit", "test-integration"} <= set(needs), (
        f"{WORKFLOW}: 'coverage' job needs must include 'test-unit' and "
        f"'test-integration' (got {needs!r})"
    )

    steps = cov_job.get("steps") or []
    run_scripts = "\n".join(str(s.get("run", "")) for s in steps)
    assert "coverage combine" in run_scripts, (
        f"{WORKFLOW}: 'coverage' job must run 'coverage combine'"
    )
    assert "coverage report --rcfile=tools/coverage-combined.toml" in run_scripts, (
        f"{WORKFLOW}: 'coverage' job must run "
        "'coverage report --rcfile=tools/coverage-combined.toml'"
    )
    assert "coverage json" in run_scripts, f"{WORKFLOW}: 'coverage' job must run 'coverage json'"

    ci_status_needs = jobs["ci-status"].get("needs") or []
    assert "coverage" in ci_status_needs, f"{WORKFLOW}: 'ci-status' needs must include 'coverage'"

    filters = load_filters(WORKFLOW)
    assert ".github/workflows/**" in filters["unit-tests"], (
        f"{WORKFLOW}: unit-tests paths-filter must include '.github/workflows/**' "
        "so workflow-only PRs run test_ci_coverage_wiring.py"
    )


def test_combined_rcfile_does_not_omit_services_or_repos() -> None:
    assert COMBINED_RCFILE.is_file(), f"Missing combined coverage config: {COMBINED_RCFILE}"
    data = tomllib.loads(COMBINED_RCFILE.read_text(encoding="utf-8"))
    cov_cfg = data.get("tool", {}).get("coverage", {})
    run_cfg = cov_cfg.get("run", {})
    report_cfg = cov_cfg.get("report", {})

    assert run_cfg.get("source") == ["src/jentic_one"], (
        f"{COMBINED_RCFILE}: [tool.coverage.run].source must be ['src/jentic_one']"
    )

    omit_entries: list[str] = [
        *run_cfg.get("omit", []),
        *report_cfg.get("omit", []),
    ]
    forbidden = [g for g in omit_entries if "repos" in g or "services" in g]
    assert not forbidden, (
        f"{COMBINED_RCFILE}: combined coverage config must not omit repos or services "
        f"(found {forbidden!r})"
    )
    assert set(omit_entries) <= {"src/jentic_one/migrations/env.py"}, (
        f"{COMBINED_RCFILE}: combined coverage omit list may only contain "
        f"'src/jentic_one/migrations/env.py' (got {omit_entries!r})"
    )
    assert "fail_under" not in report_cfg, (
        f"{COMBINED_RCFILE}: combined coverage must be report-only (no fail_under) "
        "until a baseline is recorded"
    )
    assert "tools/coverage-combined.toml" in COMMON_INPUTS, (
        "tools/ci_pass_markers.py COMMON_INPUTS must include 'tools/coverage-combined.toml'"
    )


def test_unit_omit_list_is_not_extended() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    cov_cfg = data.get("tool", {}).get("coverage", {})
    omit_entries: list[str] = [
        *cov_cfg.get("run", {}).get("omit", []),
        *cov_cfg.get("report", {}).get("omit", []),
    ]
    unexpected = _unexpected_unit_omit_globs(omit_entries)
    assert not unexpected, (
        f"{PYPROJECT}: [tool.coverage].omit grew beyond the legacy 5-glob ceiling: "
        f"{sorted(unexpected)!r}. Measure new modules in unit or combined coverage "
        "instead of extending the omit list."
    )
    assert _unexpected_unit_omit_globs(
        [*_ALLOWED_UNIT_OMIT_GLOBS, "src/jentic_one/control/services/*.py"]
    ) == {"src/jentic_one/control/services/*.py"}
