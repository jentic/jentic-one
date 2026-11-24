"""Policy loader, source-window resolver, and CI matrix generator for upgrade tests.

``supported-upgrades.yaml`` holds only the upgrade *policy* (window size,
enabled roles, backends, known breaks). The concrete source releases are
derived at run time from the repository's ``v*`` git tags and the candidate's
``pyproject.toml`` version, so the window rolls forward on every release with
no manual edit. Source image digests are resolved from GHCR when the matrix is
emitted and each leg pulls its source image by that digest.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tests" / "upgrade" / "supported-upgrades.yaml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
UPGRADE_NOTES_PATH = REPO_ROOT / "docs" / "operations" / "upgrade-notes.md"
SEED_DIR = REPO_ROOT / "tests" / "upgrade" / "seed"

#: Registry, repository, and image name of the published app image.
SOURCE_REGISTRY = "ghcr.io"
SOURCE_REPOSITORY = "jentic/jentic-one-app"
SOURCE_IMAGE = f"{SOURCE_REGISTRY}/{SOURCE_REPOSITORY}"

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_ROLE_RE = re.compile(r"^n-([1-9]\d*)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEEDER_RE = re.compile(r"^seed_v(0|[1-9]\d*)_(0|[1-9]\d*)\.py$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_INLINE_LINK_TEXT_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_NON_SLUG_CHARS_RE = re.compile(r"[^\w\s-]")

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

Version = tuple[int, int, int]
DigestResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class Source:
    """One source release the candidate is upgraded from."""

    tag: str
    role: str


def parse_version(value: str) -> Version | None:
    """Return ``(major, minor, patch)`` for a plain ``X.Y.Z`` string, else ``None``."""
    match = _SEMVER_RE.match(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


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


def _git_versions(args: list[str], repo_root: Path) -> set[str]:
    out = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    versions: set[str] = set()
    for raw in out.splitlines():
        tag = raw.strip()
        if tag.startswith("v") and parse_version(tag[1:]) is not None:
            versions.add(tag[1:])
    return versions


def git_release_tags(repo_root: Path = REPO_ROOT) -> set[str]:
    """Return semver release versions (without leading 'v') from local git tags."""
    return _git_versions(["tag", "-l", "v*"], repo_root)


def git_head_tags(repo_root: Path = REPO_ROOT) -> set[str]:
    """Return semver release versions whose ``v*`` tag points at HEAD."""
    return _git_versions(["tag", "-l", "v*", "--points-at", "HEAD"], repo_root)


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


def target_series(
    candidate_version: str,
    release_tags: set[str],
    head_tags: set[str],
) -> tuple[int, int]:
    """Return the ``(major, minor)`` series the candidate build will ship as.

    An untagged candidate version (a release PR, or a branch ahead of the last
    bump) ships as its own minor series. A candidate version that is already
    released but not tagged at HEAD is ``main`` after a release: the code under
    test ships in the next minor at the earliest supported window, so the
    target is the following minor and the just-released version becomes
    ``n-1``. The tagged release commit itself targets its own series.
    """
    parsed = parse_version(candidate_version)
    if parsed is None:
        raise ValueError(f"candidate version {candidate_version!r} is not valid semver")
    major, minor, _ = parsed
    if candidate_version in release_tags and candidate_version not in head_tags:
        return major, minor + 1
    return major, minor


def select_sources(
    target: tuple[int, int],
    release_tags: set[str],
    *,
    minors_back: int,
) -> list[Source]:
    """Return the latest released patch of each of the ``minors_back`` prior minors.

    A prior minor with no release (early history, or a major boundary) is
    omitted rather than reported as an error.
    """
    latest: dict[tuple[int, int], Version] = {}
    for tag in release_tags:
        parsed = parse_version(tag)
        if parsed is None:
            continue
        key = (parsed[0], parsed[1])
        if key not in latest or parsed > latest[key]:
            latest[key] = parsed

    sources: list[Source] = []
    major, minor = target
    for offset in range(1, minors_back + 1):
        if minor - offset < 0:
            break
        best = latest.get((major, minor - offset))
        if best is not None:
            sources.append(Source(tag=".".join(map(str, best)), role=f"n-{offset}"))
    return sources


def _parse_date(raw: Any) -> datetime.date | None:
    if isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def validate_policy(data: dict[str, Any], *, upgrade_notes_slugs: frozenset[str]) -> list[str]:
    """Return structural errors in a supported-upgrades policy document.

    Validation is static: it does not depend on the current date, the
    ``pyproject.toml`` version, or which releases exist, so a valid policy file
    stays valid as releases are cut.
    """
    errors: list[str] = []

    window = data.get("window")
    if not isinstance(window, dict):
        errors.append("window must be a mapping")
        window = {}
    minors_back = window.get("minors_back")
    if not isinstance(minors_back, int) or isinstance(minors_back, bool) or minors_back < 2:
        errors.append(f"window.minors_back must be an integer >= 2 (got {minors_back!r})")
        minors_back = 2
    if window.get("collapse_patches") != "latest":
        errors.append(
            f"window.collapse_patches must be 'latest' (got {window.get('collapse_patches')!r})"
        )
    enabled_roles = window.get("enabled_roles")
    if not isinstance(enabled_roles, list) or not enabled_roles:
        errors.append("window.enabled_roles must be a non-empty list of 'n-<k>' roles")
    else:
        for role in enabled_roles:
            match = _ROLE_RE.match(role) if isinstance(role, str) else None
            if match is None or int(match.group(1)) > minors_back:
                errors.append(
                    f"window.enabled_roles entry {role!r} must be 'n-<k>' with "
                    f"1 <= k <= minors_back ({minors_back})"
                )

    if data.get("refresh_token_policy") != "survive_unless_documented_in_upgrade_notes":
        errors.append(
            "refresh_token_policy must be 'survive_unless_documented_in_upgrade_notes' "
            f"(got {data.get('refresh_token_policy')!r})"
        )

    backends = data.get("backends")
    if not isinstance(backends, list) or not backends:
        errors.append("backends must be a non-empty list")
    else:
        backend_by_name = {
            b.get("name"): b for b in backends if isinstance(b, dict) and "name" in b
        }
        pg = backend_by_name.get("postgres")
        if not pg or pg.get("blocking") is not True or pg.get("enabled") is not True:
            errors.append("backends must include postgres with blocking=true and enabled=true")
        if "sqlite" not in backend_by_name:
            errors.append("backends must declare sqlite")

    known_breaks = data.get("known_breaks")
    if not isinstance(known_breaks, list):
        errors.append("known_breaks must be a list")
        return errors
    for kb_idx, kb in enumerate(known_breaks):
        prefix = f"known_breaks[{kb_idx}]"
        if not isinstance(kb, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        for field in ("source", "reason", "upgrade_notes_link"):
            val = kb.get(field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{prefix}.{field} must be a non-empty string")
        source = kb.get("source")
        if isinstance(source, str) and source.strip() and parse_version(source) is None:
            errors.append(f"{prefix}.source must be a release version 'X.Y.Z' (got {source!r})")

        link = kb.get("upgrade_notes_link")
        if isinstance(link, str) and link.strip():
            if "#" not in link:
                errors.append(
                    f"{prefix}.upgrade_notes_link {link!r} must include a #heading anchor"
                )
            else:
                doc_part, anchor = link.split("#", 1)
                if doc_part not in ("docs/operations/upgrade-notes.md", "upgrade-notes.md"):
                    errors.append(
                        f"{prefix}.upgrade_notes_link must target "
                        f"docs/operations/upgrade-notes.md (got {doc_part!r})"
                    )
                if anchor == "unreleased":
                    errors.append(f"{prefix}.upgrade_notes_link must not link to #unreleased")
                elif anchor not in upgrade_notes_slugs:
                    errors.append(
                        f"{prefix}.upgrade_notes_link anchor #{anchor} does not match "
                        "any heading in docs/operations/upgrade-notes.md"
                    )

        if _parse_date(kb.get("expires")) is None:
            errors.append(
                f"{prefix}.expires must be a valid YYYY-MM-DD date (got {kb.get('expires')!r})"
            )

    return errors


def active_known_break(
    data: dict[str, Any], source: str, *, today: datetime.date
) -> dict[str, Any] | None:
    """Return the unexpired ``known_breaks`` entry for ``source``, if any.

    An expired entry no longer excuses the leg: it runs as an ordinary leg and
    fails only if the migration path is still broken.
    """
    for kb in data.get("known_breaks") or []:
        if not isinstance(kb, dict) or kb.get("source") != source:
            continue
        expires = _parse_date(kb.get("expires"))
        if expires is not None and expires >= today:
            return kb
    return None


def expired_known_breaks(data: dict[str, Any], *, today: datetime.date) -> list[dict[str, Any]]:
    """Return ``known_breaks`` entries whose ``expires`` date has passed."""
    expired: list[dict[str, Any]] = []
    for kb in data.get("known_breaks") or []:
        if not isinstance(kb, dict):
            continue
        expires = _parse_date(kb.get("expires"))
        if expires is not None and expires < today:
            expired.append(kb)
    return expired


def resolve_ghcr_digest(tag: str, *, attempts: int = 3, timeout: float = 15.0) -> str | None:
    """Return the published manifest digest for ``SOURCE_IMAGE:<tag>``, or ``None``.

    ``None`` covers both "not published (yet)" and "registry unreachable"; the
    caller drops the leg with a warning either way, because neither says
    anything about the migration path.
    """
    token_url = f"https://{SOURCE_REGISTRY}/token?scope=repository:{SOURCE_REPOSITORY}:pull"
    manifest_url = f"https://{SOURCE_REGISTRY}/v2/{SOURCE_REPOSITORY}/manifests/{tag}"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(token_url, timeout=timeout) as resp:
                token = json.load(resp)["token"]
            req = urllib.request.Request(
                manifest_url,
                method="HEAD",
                headers={"Authorization": f"Bearer {token}", "Accept": _MANIFEST_ACCEPT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                digest = resp.headers.get("Docker-Content-Digest", "")
            return digest if _DIGEST_RE.match(digest) else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
            pass
    return None


def build_ci_matrix(
    data: dict[str, Any],
    sources: list[Source],
    *,
    resolve_digest: DigestResolver,
    today: datetime.date,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Return the GitHub Actions matrix payload and warnings for skipped legs.

    Only roles listed in ``window.enabled_roles`` and enabled backends produce
    legs. A source whose image digest cannot be resolved is dropped with a
    warning instead of failing the matrix.
    """
    enabled_roles = set(data.get("window", {}).get("enabled_roles") or [])
    backends = [b for b in data.get("backends", []) if b.get("enabled") is True]
    include: list[dict[str, Any]] = []
    warnings: list[str] = []
    for src in sources:
        if src.role not in enabled_roles:
            continue
        digest = resolve_digest(src.tag)
        if digest is None:
            warnings.append(
                f"source {src.tag} ({src.role}) skipped: no published image digest for "
                f"{SOURCE_IMAGE}:{src.tag} (not yet published, or the registry is unreachable)"
            )
            continue
        known_break = active_known_break(data, src.tag, today=today) is not None
        for backend in backends:
            include.append(
                {
                    "source": src.tag,
                    "role": src.role,
                    "digest": digest,
                    "backend": backend["name"],
                    "blocking": bool(backend.get("blocking", False)),
                    "known_break": known_break,
                }
            )
    for kb in expired_known_breaks(data, today=today):
        warnings.append(
            f"known_breaks entry for {kb.get('source')} expired on {kb.get('expires')}; "
            "the leg is no longer excused"
        )
    return {"include": include}, warnings


def select_seeder(source: str, seed_dir: Path = SEED_DIR) -> Path | None:
    """Return the newest ``seed_v<major>_<minor>.py`` not newer than ``source``.

    A seeder stays in force for every later source minor until a newer one is
    added, so a release that keeps the seeded API surface needs no new seeder.
    """
    parsed = parse_version(source)
    if parsed is None:
        raise ValueError(f"source version {source!r} is not valid semver")
    best: tuple[tuple[int, int], Path] | None = None
    for path in seed_dir.glob("seed_v*.py"):
        match = _SEEDER_RE.match(path.name)
        if match is None:
            continue
        key = (int(match.group(1)), int(match.group(2)))
        if key <= parsed[:2] and (best is None or key > best[0]):
            best = (key, path)
    return best[1] if best else None


def resolve_sources(data: dict[str, Any], repo_root: Path = REPO_ROOT) -> list[Source]:
    """Return the source window for this checkout from pyproject and git tags."""
    release_tags = git_release_tags(repo_root)
    target = target_series(
        read_pyproject_version(repo_root / "pyproject.toml"),
        release_tags,
        git_head_tags(repo_root),
    )
    return select_sources(target, release_tags, minors_back=data["window"]["minors_back"])


def _append(env_var: str, text: str) -> None:
    path = os.environ.get(env_var)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: validate policy, emit the CI matrix, or answer harness lookups."""
    parser = argparse.ArgumentParser(
        description="Validate supported-upgrades.yaml and resolve the upgrade-test window."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--emit-github-matrix",
        action="store_true",
        help="Resolve sources from git tags and GHCR and emit the GitHub Actions matrix.",
    )
    group.add_argument(
        "--default-source",
        action="store_true",
        help="Print the n-1 source release for this checkout.",
    )
    group.add_argument(
        "--seeder-for",
        metavar="VERSION",
        help="Print the seeder script path for a source release.",
    )
    args = parser.parse_args(argv)

    if args.seeder_for:
        seeder = select_seeder(args.seeder_for)
        if seeder is None:
            print(f"ERROR: no seeder applies to source {args.seeder_for}", file=sys.stderr)
            return 1
        print(seeder)
        return 0

    data = load_matrix()
    errors = validate_policy(data, upgrade_notes_slugs=heading_slugs())
    if errors:
        print("ERROR: supported-upgrades.yaml validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if args.default_source:
        sources = resolve_sources(data)
        if not sources:
            print("ERROR: no released source found for this checkout", file=sys.stderr)
            return 1
        print(sources[0].tag)
        return 0

    if args.emit_github_matrix:
        sources = resolve_sources(data)
        matrix, warnings = build_ci_matrix(
            data, sources, resolve_digest=resolve_ghcr_digest, today=datetime.date.today()
        )
        for warning in warnings:
            print(f"::warning::{warning}")
        if not sources:
            print("::warning::no released source versions found in git tags; no upgrade legs")
        payload = json.dumps(matrix, separators=(",", ":"))
        print(f"upgrade matrix: {payload}")
        _append(
            "GITHUB_OUTPUT", f"matrix={payload}\nhas_legs={str(bool(matrix['include'])).lower()}\n"
        )
        window = ", ".join(f"`{s.tag}` ({s.role})" for s in sources) or "none"
        legs = ", ".join(
            f"`{leg['source']}` ({leg['backend']}{', known break' if leg['known_break'] else ''})"
            for leg in matrix["include"]
        )
        summary = f"**Upgrade window:** {window}\n\n**Upgrade matrix legs:** {legs or 'none'}\n"
        if warnings:
            summary += "".join(f"\n- ⚠️ {w}" for w in warnings) + "\n"
        _append("GITHUB_STEP_SUMMARY", summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
