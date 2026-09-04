"""Drift guards pinning documented install commands to the release config.

The README and installation docs advertise package-manager one-liners
(``brew install --cask jentic/tap/jentic``, ``winget install Jentic.Jentic``,
``scoop install jentic``). The names those commands resolve — the Homebrew
tap + cask, the winget package identifier, the scoop bucket + manifest — are
all defined in one place: ``cli/.goreleaser.yaml`` (the ``homebrew_casks``,
``winget`` and ``scoops`` publishers). Nothing else ties the two together, so
a rename in the release config would silently strand every documented install
command.

These tests guard *referential* facts only — never prose, wording, or step
orderings:

- Every ``brew install`` / ``winget install`` / ``scoop`` mention of our
  packages, in any tracked markdown file (plus ``llms.txt``), must match what
  ``cli/.goreleaser.yaml`` actually publishes.
- The winget entry itself must stay internally consistent: winget-pkgs
  validation requires the manifest path (derived from ``name``) to match
  ``package_identifier``, which GoReleaser does not check.

The pipeline-side counterpart lives in ``.github/workflows/ci.yml``
(``cli-artifact-smoke``): a snapshot GoReleaser build asserts the winget and
scoop manifests are actually generated from this config.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GORELEASER_CONFIG = REPO_ROOT / "cli" / ".goreleaser.yaml"

#: Directories that hold third-party or generated markdown, not our docs.
_EXCLUDED_DIR_NAMES = {".git", ".venv", "node_modules", "dist", ".ruff_cache", ".pytest_cache"}

#: ``winget install <id>`` / ``winget upgrade <id>`` mentions.
_WINGET_RE = re.compile(r"\bwinget (?:install|upgrade)\s+([\w.]+)")

#: ``brew install [--cask] <owner>/<tap>/<name>`` mentions. Only the fully
#: qualified triple form is matched, so unrelated ``brew install foo`` lines
#: in third-party instructions never trip this.
_BREW_RE = re.compile(
    r"\bbrew (?:install|upgrade|reinstall)\s+(?:--cask\s+)?([\w.-]+/[\w.-]+/[\w.-]+)"
)

#: ``scoop bucket add <alias> <url>`` mentions.
_SCOOP_BUCKET_RE = re.compile(r"\bscoop bucket add\s+(\S+)\s+(\S+)")

#: ``scoop install <name>`` / ``scoop update <name>`` mentions (name may be
#: bucket-qualified, e.g. ``jentic/jentic``).
_SCOOP_INSTALL_RE = re.compile(r"\bscoop (?:install|update)\s+([\w./-]+)")


def _release_config() -> dict[str, Any]:
    config = yaml.safe_load(GORELEASER_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(config, dict), f"{GORELEASER_CONFIG} did not parse to a mapping"
    return config


def _doc_files() -> list[Path]:
    docs = [REPO_ROOT / "llms.txt"]
    for path in REPO_ROOT.rglob("*.md"):
        if _EXCLUDED_DIR_NAMES.isdisjoint(part.lower() for part in path.parts):
            docs.append(path)
    return [doc for doc in docs if doc.is_file()]


def _doc_lines() -> list[tuple[Path, int, str]]:
    lines: list[tuple[Path, int, str]] = []
    for doc in sorted(_doc_files()):
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            lines.append((doc, lineno, line))
    return lines


@pytest.mark.arch
def test_documented_install_commands_match_release_config() -> None:
    """Every documented package-manager command resolves what we publish.

    Ground truth is ``cli/.goreleaser.yaml``: the ``homebrew_casks`` entry
    (tap repo + cask name → the ``brew install`` ref), the ``winget`` entry
    (``package_identifier``), and the ``scoops`` entry (bucket repo + manifest
    name). A mention that drifts — a renamed identifier, a moved bucket —
    fails here with the file:line of the stale command.
    """
    config = _release_config()

    cask = config["homebrew_casks"][0]
    tap_repo = cask["repository"]
    tap_short = tap_repo["name"].removeprefix("homebrew-")
    expected_brew_ref = f"{tap_repo['owner']}/{tap_short}/{cask['name']}"

    winget = config["winget"][0]
    expected_winget_id = winget["package_identifier"]

    scoop = config["scoops"][0]
    scoop_repo = scoop["repository"]
    expected_bucket_url = f"https://github.com/{scoop_repo['owner']}/{scoop_repo['name']}"
    expected_scoop_name = scoop["name"]

    violations: list[str] = []
    for doc, lineno, line in _doc_lines():
        where = f"{doc.relative_to(REPO_ROOT)}:{lineno}"

        for ref in _BREW_RE.findall(line):
            if "jentic" in ref and ref != expected_brew_ref:
                violations.append(
                    f"{where} — `brew install {ref}` does not match the published cask "
                    f"`{expected_brew_ref}` (cli/.goreleaser.yaml homebrew_casks)"
                )

        for pkg in _WINGET_RE.findall(line):
            if "jentic" in pkg.lower() and pkg != expected_winget_id:
                violations.append(
                    f"{where} — `winget install {pkg}` does not match package_identifier "
                    f"`{expected_winget_id}` (cli/.goreleaser.yaml winget)"
                )

        for alias, url in _SCOOP_BUCKET_RE.findall(line):
            if "jentic" not in url.lower():
                continue
            if url.removesuffix(".git") != expected_bucket_url:
                violations.append(
                    f"{where} — `scoop bucket add {alias} {url}` does not match the published "
                    f"bucket `{expected_bucket_url}` (cli/.goreleaser.yaml scoops)"
                )

        for name in _SCOOP_INSTALL_RE.findall(line):
            unqualified = name.rsplit("/", 1)[-1]
            if "jentic" in unqualified.lower() and unqualified != expected_scoop_name:
                violations.append(
                    f"{where} — `scoop install {name}` does not match the published manifest "
                    f"`{expected_scoop_name}` (cli/.goreleaser.yaml scoops)"
                )

    assert not violations, (
        "Documented install commands drifted from cli/.goreleaser.yaml:\n" + "\n".join(violations)
    )


@pytest.mark.arch
def test_winget_entry_is_internally_consistent() -> None:
    """The winget entry must satisfy microsoft/winget-pkgs path validation.

    winget-pkgs requires the manifest directory
    ``manifests/<initial>/<publisher>/<name>/<version>`` to spell out the
    ``package_identifier`` (``<publisher>.<name>``). GoReleaser derives the
    path from the entry's ``publisher`` and ``name`` fields but never checks
    them against ``package_identifier`` — a mismatch ships manifests the
    upstream validation bot rejects on every release.
    """
    winget = _release_config()["winget"][0]
    expected_identifier = f"{winget['publisher']}.{winget['name']}"
    assert winget["package_identifier"] == expected_identifier, (
        f"cli/.goreleaser.yaml winget: package_identifier "
        f"{winget['package_identifier']!r} != publisher.name "
        f"({expected_identifier!r}) — winget-pkgs validation rejects manifests whose "
        "directory path does not match the identifier"
    )

    base = winget["repository"]["pull_request"]["base"]
    assert (base["owner"], base["name"]) == ("microsoft", "winget-pkgs"), (
        "cli/.goreleaser.yaml winget: the pull request must target "
        f"microsoft/winget-pkgs, not {base['owner']}/{base['name']} — otherwise "
        "`winget install` users never see the release"
    )
