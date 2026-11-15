"""Unit tests for the supported-upgrades matrix and CI matrix generator."""

from __future__ import annotations

import copy
import datetime
from pathlib import Path

import pytest

from tests.upgrade.matrix import (
    build_ci_matrix,
    heading_slugs,
    load_matrix,
    read_pyproject_version,
    validate_supported_upgrades,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tests" / "upgrade" / "supported-upgrades.yaml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
UPGRADE_NOTES_PATH = REPO_ROOT / "docs" / "operations" / "upgrade-notes.md"


def test_supported_upgrades_yaml_is_valid() -> None:
    """The checked-in supported-upgrades.yaml passes structural and anchor validation."""
    data = load_matrix(MATRIX_PATH)
    errors = validate_supported_upgrades(
        data,
        pyproject_version=read_pyproject_version(PYPROJECT_PATH),
        upgrade_notes_slugs=heading_slugs(UPGRADE_NOTES_PATH),
        today=datetime.date.today(),
    )
    assert not errors, "supported-upgrades.yaml validation failed:\n" + "\n".join(errors)


@pytest.mark.parametrize("pyproject_ver", ["0.44.0", "0.45.0"])
def test_supported_upgrades_accepts_main_and_release_pr_versions(pyproject_ver: str) -> None:
    """The matrix validates both before and after release-please bumps pyproject.toml."""
    data = load_matrix(MATRIX_PATH)
    errors = validate_supported_upgrades(
        data,
        pyproject_version=pyproject_ver,
        upgrade_notes_slugs=heading_slugs(UPGRADE_NOTES_PATH),
        today=datetime.date(2026, 11, 15),
    )
    assert not errors


def test_removing_required_source_minor_fails_validation() -> None:
    """Omitting N-1 (0.44.x) or N-2 (0.43.x) from sources fails structural validation."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["targets"][0]["sources"] = [
        s for s in data["targets"][0]["sources"] if s["tag"] != "0.44.0"
    ]
    errors = validate_supported_upgrades(
        data,
        pyproject_version="0.45.0",
        upgrade_notes_slugs=heading_slugs(UPGRADE_NOTES_PATH),
        today=datetime.date(2026, 11, 15),
    )
    assert any("omits required N-1 minor series '0.44.x'" in err for err in errors), errors


def test_unknown_tag_or_malformed_digest_fails_validation() -> None:
    """A source tag missing from the supplied release tags or a bad digest fails validation."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["targets"][0]["sources"].append(
        {
            "tag": "0.99.9",
            "image": "ghcr.io/jentic/jentic-one-app:0.99.9",
            "digest": "sha256:not-a-hex-digest",
            "role": "extra",
            "phase1_enabled": False,
        }
    )
    errors = validate_supported_upgrades(
        data,
        pyproject_version="0.45.0",
        upgrade_notes_slugs=heading_slugs(UPGRADE_NOTES_PATH),
        today=datetime.date(2026, 11, 15),
        release_tags={"0.43.0", "0.44.0"},
    )
    assert any("does not match any git tag 'v0.99.9'" in err for err in errors), errors
    assert any("must be a 'sha256:<64-hex>' digest" in err for err in errors), errors


def test_expired_or_unlinked_known_break_fails_validation() -> None:
    """An expired known_breaks entry or a broken upgrade-notes anchor fails validation."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["targets"][0]["known_breaks"] = [
        {
            "source": "0.43.0",
            "reason": "Simulated intentional break",
            "upgrade_notes_link": "docs/operations/upgrade-notes.md#0430",
            "expires": "2026-01-01",
        },
        {
            "source": "0.43.0",
            "reason": "Missing heading anchor",
            "upgrade_notes_link": "docs/operations/upgrade-notes.md#nonexistent-heading",
            "expires": "2099-01-01",
        },
    ]
    errors = validate_supported_upgrades(
        data,
        pyproject_version="0.45.0",
        upgrade_notes_slugs=heading_slugs(UPGRADE_NOTES_PATH),
        today=datetime.date(2026, 11, 15),
    )
    assert any("expires (2026-01-01) has expired" in err for err in errors), errors
    assert any("does not match any heading" in err for err in errors), errors


def test_build_ci_matrix_emits_phase1_enabled_legs() -> None:
    """The CI matrix generator emits only enabled source and backend combinations."""
    data = load_matrix(MATRIX_PATH)
    matrix = build_ci_matrix(data)
    expected_digest = "".join(
        [
            "sha256:075dae4771825300fac7bd9c7524ffe6",  # pragma: allowlist secret
            "3c2e01866e91aa1ee63cc35a54bb466b",  # pragma: allowlist secret
        ]
    )
    assert matrix == {
        "include": [
            {
                "source": "0.44.0",
                "digest": expected_digest,
                "backend": "postgres",
                "blocking": True,
            }
        ]
    }
