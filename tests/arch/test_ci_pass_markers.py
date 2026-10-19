"""Pin ``tools/ci_pass_markers.py`` to the CI workflow it reads.

The pass-marker skip is only safe while each job's declared inputs (its
paths-filter groups in ``.github/workflows/ci.yml``) are the same globs the
hash covers, and while every marker id the tool computes is the one the
workflow reads back. These tests fail when either side drifts.
"""

from __future__ import annotations

import pytest
from tools.ci_pass_markers import (
    JOBS,
    WORKFLOW,
    glob_to_regex,
    inputs_hash,
    load_filters,
    readable_refs,
)

pytestmark = pytest.mark.arch


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("src/**", "src/jentic_one/a.py", True),
        ("src/**", "srcx/a.py", False),
        ("tests/**/conftest.py", "tests/conftest.py", True),
        ("tests/**/conftest.py", "tests/web/auth/conftest.py", True),
        ("tests/**/conftest.py", "tests/web/auth/test_x.py", False),
        ("cli/internal/skillgen/content/*.md", "cli/internal/skillgen/content/a.md", True),
        ("cli/internal/skillgen/content/*.md", "cli/internal/skillgen/content/x/a.md", False),
        ("scripts/spa_packaging_smoke.*", "scripts/spa_packaging_smoke.sh", True),
        ("deploy/docker/*.Dockerfile", "deploy/docker/app.Dockerfile", True),
        ("uv.lock", "uv.lock", True),
        ("uv.lock", "uvXlock", False),
    ],
)
def test_glob_translation_matches_paths_filter_semantics(
    glob: str, path: str, matches: bool
) -> None:
    assert bool(glob_to_regex(glob).match(path)) is matches


def test_every_marker_job_reads_existing_filter_groups() -> None:
    filters = load_filters()
    for job, groups in JOBS.items():
        missing = [g for g in groups if g not in filters]
        assert not missing, f"{job} names paths-filter groups not in {WORKFLOW}: {missing}"


def test_every_marker_id_is_read_back_by_the_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in JOBS:
        direct = f"['{job}']" in text
        via_matrix = any(job.startswith(prefix) for prefix in _format_prefixes(text))
        assert direct or via_matrix, (
            f"{WORKFLOW} never reads the {job!r} pass marker "
            "(fromJSON(needs.changes.outputs.skips)['<id>'] or a format('<prefix>{0}', …))"
        )


def _format_prefixes(text: str) -> list[str]:
    marker = "format('"
    prefixes = []
    start = text.find(marker)
    while start != -1:
        end = text.find("{0}", start)
        prefixes.append(text[start + len(marker) : end])
        start = text.find(marker, end)
    return prefixes


def test_hash_tracks_only_matched_files() -> None:
    tracked = ["100644 aaa 0\tsrc/a.py", "100644 bbb 0\tdocs/x.md"]
    base = inputs_hash(["src/**"], tracked)
    assert inputs_hash(["src/**"], ["100644 aaa 0\tsrc/a.py", "100644 ccc 0\tdocs/x.md"]) == base
    assert inputs_hash(["src/**"], ["100644 abc 0\tsrc/a.py", "100644 bbb 0\tdocs/x.md"]) != base
    assert inputs_hash(["src/**"], [*tracked, "100644 ddd 0\tsrc/new.py"]) != base
    assert inputs_hash(["src/**"], tracked, salt="image-2") != base


def test_readable_refs_cover_own_ref_and_default_branch() -> None:
    env = {
        "GITHUB_REF": "refs/pull/7/merge",
        "GITHUB_BASE_REF": "main",
        "DEFAULT_BRANCH": "main",
    }
    assert readable_refs(env) == ["refs/pull/7/merge", "refs/heads/main"]
    assert readable_refs({"GITHUB_REF": "refs/heads/main", "DEFAULT_BRANCH": "main"}) == [
        "refs/heads/main"
    ]
