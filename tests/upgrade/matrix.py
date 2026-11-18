"""Shared loader, validator, and CI matrix generator for supported-upgrades.yaml."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tests" / "upgrade" / "supported-upgrades.yaml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
UPGRADE_NOTES_PATH = REPO_ROOT / "docs" / "operations" / "upgrade-notes.md"

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SERIES_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_INLINE_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_NON_SLUG_CHARS_RE = re.compile(r"[^\w\s-]")


def load_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    """Parse supported-upgrades.yaml into a dictionary."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must parse to a YAML mapping")
    return data


def read_pyproject_version(path: Path = PYPROJECT_PATH) -> str:
    """Return [project].version from pyproject.toml."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str):
        raise ValueError(f"{path} missing [project].version")
    return version


def git_release_tags(repo_root: Path = REPO_ROOT) -> set[str]:
    """Return semver release versions (without leading 'v') from local git tags."""
    out = subprocess.run(
        ["git", "tag", "-l", "v*"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tags: set[str] = set()
    for raw in out.splitlines():
        tag = raw.strip()
        if tag.startswith("v") and _SEMVER_RE.match(tag[1:]):
            tags.add(tag[1:])
    return tags


def heading_slugs(doc: Path = UPGRADE_NOTES_PATH) -> frozenset[str]:
    """Extract GitHub-style heading anchor slugs from a Markdown file."""
    counts: dict[str, int] = {}
    slugs: set[str] = set()
    in_fence = False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        text = _INLINE_LINK_TEXT_RE.sub(r"\1", match.group(1))
        slug = _NON_SLUG_CHARS_RE.sub("", text.lower())
        slug = re.sub(r"\s", "-", slug)
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return frozenset(slugs)


def _latest_patch_by_minor(release_tags: set[str]) -> dict[tuple[int, int], str]:
    best: dict[tuple[int, int], tuple[int, str]] = {}
    for ver in release_tags:
        m = _SEMVER_RE.match(ver)
        if not m:
            continue
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        key = (major, minor)
        if key not in best or patch > best[key][0]:
            best[key] = (patch, ver)
    return {key: val[1] for key, val in best.items()}


def validate_supported_upgrades(
    data: dict[str, Any],
    *,
    pyproject_version: str,
    upgrade_notes_slugs: frozenset[str],
    today: datetime.date,
    release_tags: set[str] | None = None,
) -> list[str]:
    """Return validation errors for a supported-upgrades YAML document.

    Structural validation runs without git history when ``release_tags`` is
    ``None``. Passing ``release_tags`` additionally checks that every source tag
    exists as a ``v<tag>`` git tag and matches the latest patch of its minor
    series.
    """
    errors: list[str] = []

    window = data.get("window")
    minors_back = 2
    if not isinstance(window, dict):
        errors.append("window must be a mapping")
    else:
        raw_minors_back = window.get("minors_back")
        if not isinstance(raw_minors_back, int) or raw_minors_back < 2:
            errors.append(f"window.minors_back must be an integer >= 2 (got {raw_minors_back!r})")
        else:
            minors_back = raw_minors_back
        if window.get("collapse_patches") != "latest":
            errors.append(
                f"window.collapse_patches must be 'latest' (got {window.get('collapse_patches')!r})"
            )

    if data.get("refresh_token_policy") != "survive_unless_documented_in_upgrade_notes":
        errors.append(
            "refresh_token_policy must be 'survive_unless_documented_in_upgrade_notes' "
            f"(got {data.get('refresh_token_policy')!r})"
        )

    py_match = _SEMVER_RE.match(pyproject_version)
    if not py_match:
        errors.append(f"pyproject.toml version {pyproject_version!r} is not valid semver")
        return errors
    py_major, py_minor = int(py_match.group(1)), int(py_match.group(2))

    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        errors.append("targets must be a non-empty list")
        return errors

    latest_patches = _latest_patch_by_minor(release_tags) if release_tags is not None else {}
    allowed_target_series = {
        f"{py_major}.{py_minor}",
        f"{py_major}.{py_minor + 1}",
    }

    matched_active_target = False
    for idx, target in enumerate(targets):
        prefix = f"targets[{idx}]"
        if not isinstance(target, dict):
            errors.append(f"{prefix} must be a mapping")
            continue

        series = target.get("version_series")
        if not isinstance(series, str) or not _SERIES_RE.match(series):
            errors.append(f"{prefix}.version_series must be 'MAJOR.MINOR' (got {series!r})")
            continue

        s_major, s_minor = (int(part) for part in series.split("."))
        if series in allowed_target_series:
            matched_active_target = True

        sources = target.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{prefix}.sources must be a non-empty list")
            continue

        seen_tags: set[str] = set()
        by_minor: dict[tuple[int, int], list[tuple[str, Any]]] = {}
        for s_idx, src in enumerate(sources):
            s_prefix = f"{prefix}.sources[{s_idx}]"
            if not isinstance(src, dict):
                errors.append(f"{s_prefix} must be a mapping")
                continue
            tag = src.get("tag")
            tag_match = _SEMVER_RE.match(tag) if isinstance(tag, str) else None
            if not isinstance(tag, str) or tag_match is None:
                errors.append(f"{s_prefix}.tag must be valid semver (got {tag!r})")
                continue
            if tag in seen_tags:
                errors.append(f"{s_prefix}.tag {tag!r} is duplicated")
            seen_tags.add(tag)

            t_major, t_minor = int(tag_match.group(1)), int(tag_match.group(2))
            by_minor.setdefault((t_major, t_minor), []).append((tag, src.get("role")))

            if release_tags is not None and tag not in release_tags:
                errors.append(f"{s_prefix}.tag {tag!r} does not match any git tag 'v{tag}'")

            expected_image = f"ghcr.io/jentic/jentic-one-app:{tag}"
            if src.get("image") != expected_image:
                errors.append(
                    f"{s_prefix}.image must be {expected_image!r} (got {src.get('image')!r})"
                )

            digest = src.get("digest")
            if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
                errors.append(
                    f"{s_prefix}.digest must be a 'sha256:<64-hex>' digest (got {digest!r})"
                )

            if not isinstance(src.get("phase1_enabled"), bool):
                errors.append(f"{s_prefix}.phase1_enabled must be a boolean")

        for offset in range(1, minors_back + 1):
            req_series = (s_major, s_minor - offset)
            expected_role = f"n-{offset}"
            entries = by_minor.get(req_series, [])
            if not entries:
                errors.append(
                    f"{prefix}.sources omits required N-{offset} minor series "
                    f"'{req_series[0]}.{req_series[1]}.x'"
                )
            else:
                if len(entries) > 1:
                    errors.append(
                        f"{prefix}.sources must collapse {req_series[0]}.{req_series[1]}.x "
                        f"to a single patch release (got {[t for t, _ in entries]!r})"
                    )
                entry_tag, entry_role = entries[0]
                if entry_role != expected_role:
                    errors.append(
                        f"{prefix}.sources entry {entry_tag!r} for "
                        f"{req_series[0]}.{req_series[1]}.x must have role={expected_role!r} "
                        f"(got {entry_role!r})"
                    )

            if release_tags is not None:
                expected_tag = latest_patches.get(req_series)
                if expected_tag is None:
                    errors.append(
                        f"{prefix}: no released git tag found for required minor series "
                        f"{req_series[0]}.{req_series[1]}"
                    )
                elif expected_tag not in seen_tags:
                    errors.append(
                        f"{prefix}.sources omits required N-{offset} release {expected_tag!r} "
                        f"(latest patch of {req_series[0]}.{req_series[1]})"
                    )

        backends = target.get("backends")
        if not isinstance(backends, list) or not backends:
            errors.append(f"{prefix}.backends must be a non-empty list")
        else:
            backend_by_name = {
                b.get("name"): b for b in backends if isinstance(b, dict) and "name" in b
            }
            pg = backend_by_name.get("postgres")
            if not pg or pg.get("blocking") is not True or pg.get("enabled") is not True:
                errors.append(
                    f"{prefix}.backends must include postgres with blocking=true and enabled=true"
                )
            if "sqlite" not in backend_by_name:
                errors.append(f"{prefix}.backends must declare sqlite")

        known_breaks = target.get("known_breaks")
        if not isinstance(known_breaks, list):
            errors.append(f"{prefix}.known_breaks must be a list")
        else:
            for kb_idx, kb in enumerate(known_breaks):
                kb_prefix = f"{prefix}.known_breaks[{kb_idx}]"
                if not isinstance(kb, dict):
                    errors.append(f"{kb_prefix} must be a mapping")
                    continue
                for field in ("source", "reason", "upgrade_notes_link"):
                    val = kb.get(field)
                    if not isinstance(val, str) or not val.strip():
                        errors.append(f"{kb_prefix}.{field} must be a non-empty string")

                link = kb.get("upgrade_notes_link")
                if isinstance(link, str) and link.strip():
                    if "#" not in link:
                        errors.append(
                            f"{kb_prefix}.upgrade_notes_link {link!r} must include "
                            "a #heading anchor"
                        )
                    else:
                        doc_part, anchor = link.split("#", 1)
                        if doc_part not in (
                            "docs/operations/upgrade-notes.md",
                            "upgrade-notes.md",
                        ):
                            errors.append(
                                f"{kb_prefix}.upgrade_notes_link must target "
                                f"docs/operations/upgrade-notes.md (got {doc_part!r})"
                            )
                        if anchor == "unreleased":
                            errors.append(
                                f"{kb_prefix}.upgrade_notes_link must not link to #unreleased"
                            )
                        elif anchor not in upgrade_notes_slugs:
                            errors.append(
                                f"{kb_prefix}.upgrade_notes_link anchor #{anchor} does not match "
                                "any heading in docs/operations/upgrade-notes.md"
                            )

                raw_expires = kb.get("expires")
                parsed_expires: datetime.date | None = None
                if isinstance(raw_expires, datetime.date) and not isinstance(
                    raw_expires, datetime.datetime
                ):
                    parsed_expires = raw_expires
                elif isinstance(raw_expires, str):
                    try:
                        parsed_expires = datetime.date.fromisoformat(raw_expires)
                    except ValueError:
                        parsed_expires = None

                if parsed_expires is None:
                    errors.append(
                        f"{kb_prefix}.expires must be a valid YYYY-MM-DD date (got {raw_expires!r})"
                    )
                elif parsed_expires < today:
                    errors.append(
                        f"{kb_prefix}.expires ({parsed_expires.isoformat()}) has expired "
                        f"(today is {today.isoformat()})"
                    )

    if not matched_active_target:
        errors.append(
            f"targets must include an entry for {sorted(allowed_target_series)!r} "
            f"matching pyproject.toml version {pyproject_version!r}"
        )

    return errors


def build_ci_matrix(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build the GitHub Actions matrix payload for enabled (source, backend) legs."""
    include: list[dict[str, Any]] = []
    for target in data.get("targets", []):
        sources = [s for s in target.get("sources", []) if s.get("phase1_enabled") is True]
        backends = [b for b in target.get("backends", []) if b.get("enabled") is True]
        for src in sources:
            for backend in backends:
                include.append(
                    {
                        "source": src["tag"],
                        "digest": src["digest"],
                        "backend": backend["name"],
                        "blocking": bool(backend.get("blocking", False)),
                    }
                )
    return {"include": include}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for validating and emitting the upgrade matrix."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate supported-upgrades.yaml and optionally emit the GitHub Actions matrix."
        )
    )
    parser.add_argument(
        "--verify-git-tags",
        action="store_true",
        help="Verify that every source tag exists as a local 'v*' git tag.",
    )
    parser.add_argument(
        "--emit-github-matrix",
        action="store_true",
        help="Emit the enabled matrix legs as JSON (and write to GITHUB_OUTPUT when set).",
    )
    args = parser.parse_args(argv)

    data = load_matrix()
    tags: set[str] | None = None
    if args.verify_git_tags:
        tags = git_release_tags()
        if not tags:
            print(
                "ERROR: --verify-git-tags requested but no 'v*' git tags were found",
                file=sys.stderr,
            )
            return 1

    errors = validate_supported_upgrades(
        data,
        pyproject_version=read_pyproject_version(),
        upgrade_notes_slugs=heading_slugs(),
        today=datetime.date.today(),
        release_tags=tags,
    )
    if errors:
        print("ERROR: supported-upgrades.yaml validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if args.emit_github_matrix:
        matrix = build_ci_matrix(data)
        if not matrix["include"]:
            print(
                "ERROR: no enabled upgrade matrix legs found in supported-upgrades.yaml",
                file=sys.stderr,
            )
            return 1
        payload = json.dumps(matrix, separators=(",", ":"))
        print(f"upgrade matrix: {payload}")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as fh:
                fh.write(f"matrix={payload}\n")
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            legs = ", ".join(f"`{leg['source']}` ({leg['backend']})" for leg in matrix["include"])
            with open(step_summary, "a", encoding="utf-8") as fh:
                fh.write(f"**Upgrade matrix legs:** {legs}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
