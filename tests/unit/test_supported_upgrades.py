"""Unit tests for the upgrade-test policy, source-window resolver, and CI matrix."""

from __future__ import annotations

import copy
import datetime
from pathlib import Path

import pytest

from tests.upgrade.matrix import (
    SEED_DIR,
    Source,
    build_ci_matrix,
    heading_slugs,
    load_matrix,
    select_seeder,
    select_sources,
    target_series,
    validate_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tests" / "upgrade" / "supported-upgrades.yaml"
UPGRADE_NOTES_PATH = REPO_ROOT / "docs" / "operations" / "upgrade-notes.md"

TODAY = datetime.date(2026, 11, 24)
DIGEST = "sha256:" + "a" * 64  # pragma: allowlist secret

#: Release tags as of 0.45.1, plus the tags later releases add.
TAGS_0_45_1 = {"0.42.0", "0.43.0", "0.44.0", "0.45.0", "0.45.1"}
TAGS_0_46_0 = TAGS_0_45_1 | {"0.46.0"}
TAGS_0_46_2 = TAGS_0_46_0 | {"0.46.1", "0.46.2"}


def _window(candidate: str, tags: set[str], head_tags: set[str] | None = None) -> list[Source]:
    target = target_series(candidate, tags, head_tags or set())
    return select_sources(target, tags, minors_back=2)


def _slugs() -> frozenset[str]:
    return heading_slugs(UPGRADE_NOTES_PATH)


def test_checked_in_policy_is_valid() -> None:
    """The checked-in supported-upgrades.yaml passes structural and anchor validation."""
    errors = validate_policy(load_matrix(MATRIX_PATH), upgrade_notes_slugs=_slugs())
    assert not errors, "supported-upgrades.yaml validation failed:\n" + "\n".join(errors)


@pytest.mark.parametrize(
    ("candidate", "tags", "head_tags", "expected"),
    [
        # main after 0.45.1 shipped: the next release is at least 0.46.
        ("0.45.1", TAGS_0_45_1, set(), [("0.45.1", "n-1"), ("0.44.0", "n-2")]),
        # release-please PR for 0.46.0.
        ("0.46.0", TAGS_0_45_1, set(), [("0.45.1", "n-1"), ("0.44.0", "n-2")]),
        # the tagged v0.46.0 commit in release.yml never upgrades from itself.
        ("0.46.0", TAGS_0_46_0, {"0.46.0"}, [("0.45.1", "n-1"), ("0.44.0", "n-2")]),
        # main after 0.46.0 shipped.
        ("0.46.0", TAGS_0_46_0, set(), [("0.46.0", "n-1"), ("0.45.1", "n-2")]),
        # release-please PR for 0.47.0 after 0.46.x patches: latest patch wins.
        ("0.47.0", TAGS_0_46_2, set(), [("0.46.2", "n-1"), ("0.45.1", "n-2")]),
        # a patch release PR targets its own series.
        ("0.45.2", TAGS_0_45_1, set(), [("0.44.0", "n-1"), ("0.43.0", "n-2")]),
    ],
)
def test_window_rolls_with_releases(
    candidate: str,
    tags: set[str],
    head_tags: set[str],
    expected: list[tuple[str, str]],
) -> None:
    """The source window follows pyproject and git tags with no file edits."""
    assert [(s.tag, s.role) for s in _window(candidate, tags, head_tags)] == expected


def test_window_omits_minors_with_no_release() -> None:
    """A prior minor with no release is omitted instead of failing."""
    assert _window("0.1.0", {"0.0.3"}) == [Source(tag="0.0.3", role="n-1")]
    assert _window("1.0.0", {"0.9.0"}) == []


def test_policy_validation_does_not_depend_on_the_date() -> None:
    """An expired known break is not a validation error, so it cannot fail unrelated PRs."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["known_breaks"] = [
        {
            "source": "0.43.0",
            "reason": "Simulated intentional break",
            "upgrade_notes_link": "docs/operations/upgrade-notes.md#0430",
            "expires": "2020-01-01",
        }
    ]
    assert validate_policy(data, upgrade_notes_slugs=_slugs()) == []


def test_malformed_known_break_fails_validation() -> None:
    """A known break with a bad source, anchor, or date fails validation."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["known_breaks"] = [
        {
            "source": "latest",
            "reason": "Missing heading anchor",
            "upgrade_notes_link": "docs/operations/upgrade-notes.md#nonexistent-heading",
            "expires": "someday",
        }
    ]
    errors = validate_policy(data, upgrade_notes_slugs=_slugs())
    assert any("source must be a release version" in err for err in errors), errors
    assert any("does not match any heading" in err for err in errors), errors
    assert any("expires must be a valid YYYY-MM-DD date" in err for err in errors), errors


def test_policy_requires_a_blocking_postgres_backend_and_valid_roles() -> None:
    """Disabling postgres or naming a role outside the window fails validation."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["backends"] = [b for b in data["backends"] if b["name"] != "postgres"]
    data["window"]["enabled_roles"] = ["n-3"]
    errors = validate_policy(data, upgrade_notes_slugs=_slugs())
    assert any("postgres with blocking=true" in err for err in errors), errors
    assert any("enabled_roles entry 'n-3'" in err for err in errors), errors


def test_build_ci_matrix_emits_enabled_roles_with_resolved_digests() -> None:
    """Only enabled roles and backends produce legs, each pinned to its digest."""
    data = load_matrix(MATRIX_PATH)
    sources = [Source("0.45.1", "n-1"), Source("0.44.0", "n-2")]
    matrix, warnings = build_ci_matrix(
        data, sources, resolve_digest=lambda _tag: DIGEST, today=TODAY
    )
    assert warnings == []
    assert matrix == {
        "include": [
            {
                "source": "0.45.1",
                "role": "n-1",
                "digest": DIGEST,
                "backend": "postgres",
                "blocking": True,
                "known_break": False,
            }
        ]
    }


def test_unpublished_source_is_skipped_with_a_warning() -> None:
    """A tag whose image is not on GHCR yet drops the leg instead of failing."""
    data = load_matrix(MATRIX_PATH)
    matrix, warnings = build_ci_matrix(
        data, [Source("0.46.0", "n-1")], resolve_digest=lambda _tag: None, today=TODAY
    )
    assert matrix == {"include": []}
    assert len(warnings) == 1
    assert "0.46.0" in warnings[0]


def test_known_break_excuses_a_leg_until_it_expires() -> None:
    """An active known break marks the leg; an expired one gates again and warns."""
    data = copy.deepcopy(load_matrix(MATRIX_PATH))
    data["known_breaks"] = [
        {
            "source": "0.45.1",
            "reason": "Simulated intentional break",
            "upgrade_notes_link": "docs/operations/upgrade-notes.md#0430",
            "expires": "2026-12-31",
        }
    ]
    sources = [Source("0.45.1", "n-1")]

    active, _ = build_ci_matrix(data, sources, resolve_digest=lambda _t: DIGEST, today=TODAY)
    assert active["include"][0]["known_break"] is True

    later = datetime.date(2027, 1, 1)
    expired, warnings = build_ci_matrix(
        data, sources, resolve_digest=lambda _t: DIGEST, today=later
    )
    assert expired["include"][0]["known_break"] is False
    assert any("expired" in w for w in warnings), warnings


@pytest.mark.parametrize("source", ["0.44.0", "0.45.1", "0.46.0", "0.52.3"])
def test_existing_seeder_serves_later_sources(source: str) -> None:
    """The newest seeder not newer than the source applies, so new minors need no edit."""
    assert select_seeder(source) == SEED_DIR / "seed_v0_44.py"


def test_newest_applicable_seeder_wins(tmp_path: Path) -> None:
    """A newer seeder takes over from its minor onward; older sources keep the old one."""
    for name in ("seed_v0_44.py", "seed_v0_48.py", "seed_helpers.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert select_seeder("0.47.2", tmp_path) == tmp_path / "seed_v0_44.py"
    assert select_seeder("0.48.0", tmp_path) == tmp_path / "seed_v0_48.py"
    assert select_seeder("0.43.0", tmp_path) is None
