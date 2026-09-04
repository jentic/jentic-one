"""Drift guard pinning cosign identity references to the release workflow.

The docs and ``tools/install.sh`` tell verifiers to pin cosign's
``--certificate-identity`` / ``--certificate-identity-regexp`` to the GitHub
workflow that signs our artifacts (e.g.
``https://github.com/jentic/jentic-one/.github/workflows/release.yml@refs/tags/…``).
The identity embeds a workflow *path*: if that workflow is renamed or moved,
every documented verification command starts rejecting genuine artifacts —
and nothing else ties the two together.

This gate extracts the ``.github/workflows/<name>`` segment from every
identity value in tracked markdown files and ``tools/install.sh`` and asserts
the workflow file exists in the repo. Segment existence only — the OIDC
issuer, repo owner, and ref pattern are cosign's concern, not a referential
fact this repo can check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

INSTALL_SCRIPT = REPO_ROOT / "tools" / "install.sh"

#: Lines that carry a cosign identity value: the flag itself in docs, or the
#: ``COSIGN_CERT_IDENTITY*`` variables that feed the flag in install.sh.
_IDENTITY_LINE_RE = re.compile(r"certificate-identity|COSIGN_CERT_IDENTITY")

#: The workflow-path segment inside an identity value. Values are regex
#: patterns, so dots arrive escaped (``release\.yml`` — doubly so in shell:
#: ``release\\.yml``); the character class keeps the backslashes and the
#: match is unescaped before the existence check.
_WORKFLOW_SEGMENT_RE = re.compile(r"\.github/workflows/[\w\\.-]+")


def _identity_lines() -> list[tuple[Path, int, str]]:
    """``(file, lineno, line)`` for every identity-bearing line in scope."""
    files = [INSTALL_SCRIPT]
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    files.extend(REPO_ROOT / name for name in tracked.split("\0") if name)

    lines: list[tuple[Path, int, str]] = []
    for path in sorted(set(files)):
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _IDENTITY_LINE_RE.search(line):
                lines.append((path, lineno, line))
    return lines


@pytest.mark.arch
def test_cosign_identity_workflow_paths_exist() -> None:
    """Every cosign identity must name a workflow file that exists.

    A renamed or removed signing workflow fails here with the file:line of
    every identity that still pins the old path, instead of shipping
    verification commands that reject genuine release artifacts.
    """
    violations: list[str] = []
    segments_found = 0
    for path, lineno, line in _identity_lines():
        for segment in _WORKFLOW_SEGMENT_RE.findall(line):
            segments_found += 1
            workflow = REPO_ROOT / re.sub(r"\\+", "", segment)
            if not workflow.is_file():
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} — cosign identity pins "
                    f"{segment!r}, but {workflow.relative_to(REPO_ROOT)} does not exist "
                    "(workflow renamed? update every documented verify command and "
                    "tools/install.sh)"
                )

    assert segments_found > 0, (
        "No cosign identity workflow paths found in tracked *.md or tools/install.sh — "
        "either the identity pinning moved (update this gate's file set) or the "
        "extraction regexes rotted"
    )
    assert not violations, "Cosign identities pin missing workflow paths:\n" + "\n".join(violations)
