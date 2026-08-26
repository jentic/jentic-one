"""Drift guards for the static agent-facing docs (``llms.txt``, ``AGENTS.md``).

These two repo-root documents tell a coding agent how to evaluate, install and
use Jentic One. Unlike the runtime-served llms.txt (rendered by
``shared/web/agent_discovery.py`` and pinned by its own unit tests) and the
agent skill (byte-drift-guarded by ``test_skill_drift.py``), nothing pinned the
static docs to the code they describe — which is how a quickstart example
rotted into an instruction that cannot work (the broker is a forward proxy;
``GET:/get`` resolves upstream host ``get``).

These tests guard *referential* facts only — never prose, wording, or step
orderings — using committed artifacts that are themselves drift-guarded
against code as ground truth:

- ``ui/public/cli-reference.json`` is pinned to the cobra command tree by the
  Go test ``TestCommittedCLIReferenceUpToDate`` (``make cli-reference``), so a
  ``jentic``/``jenticctl`` command mentioned in the docs can be validated
  against it without building Go here.
- Links into this repository (raw.githubusercontent.com or relative) must
  point at committed paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

AGENT_DOCS = [REPO_ROOT / "llms.txt", REPO_ROOT / "AGENTS.md"]

CLI_REFERENCE = REPO_ROOT / "ui" / "public" / "cli-reference.json"

#: Links into this repo's files on GitHub: the ``refs/heads/<branch>``,
#: ``refs/tags/<tag>`` and bare ``<ref>`` raw-URL forms are all in use. The
#: capture must not end in ``.`` so sentence punctuation is not swallowed.
_RAW_LINK_RE = re.compile(
    r"https://raw\.githubusercontent\.com/jentic/jentic-one/"
    r"(?:refs/(?:heads|tags)/)?[\w.-]+/([\w./-]*[\w-])"
)

#: Relative markdown link targets, e.g. ``[text](docs/guides/first-call.md)``.
#: Root-absolute targets (``](/app)``) are excluded: joining them onto
#: ``REPO_ROOT`` would silently discard the root and check the host filesystem.
_MD_LINK_RE = re.compile(r"\]\((?!https?://|#|mailto:|/)([\w./-]+?)(?:#[^)]*)?\)")

#: Backticked repo paths mentioned in prose, e.g. ```docs/security/security.md``.
#: Requires a ``/`` and a file extension so plain filenames and flags don't match.
_PATH_MENTION_RE = re.compile(r"`([\w-]+(?:/[\w.-]+)+\.(?:md|ya?ml|json|sh|txt|js|py|toml))`")

#: CLI command mentions, e.g. ```jentic access request`` or ```jenticctl install``.
#: Longest alternative first — ``jentic|jenticctl`` would match the ``jentic``
#: prefix of ``jenticctl`` and never backtrack, exempting every jenticctl
#: mention. Only lowercase word tokens count as command segments; args, flags
#: and placeholders (``<...>``, ``--flag``, ``GET:...``) end the match.
_CLI_MENTION_RE = re.compile(r"`((?:jenticctl|jentic)\b(?: [a-z][a-z-]+)*)")


def _command_paths() -> frozenset[str]:
    """Every command path in the committed CLI reference, e.g. ``jentic access``."""
    reference = json.loads(CLI_REFERENCE.read_text(encoding="utf-8"))

    def walk(node: dict[str, Any], prefix: str) -> set[str]:
        full = f"{prefix} {node['name']}".strip()
        found = {full}
        for child in node.get("commands") or []:
            found |= walk(child, full)
        return found

    paths: set[str] = set()
    for binary in reference["binaries"]:
        paths |= walk(binary, "")
    return frozenset(paths)


def _doc_lines() -> list[tuple[Path, int, str]]:
    lines: list[tuple[Path, int, str]] = []
    for doc in AGENT_DOCS:
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            lines.append((doc, lineno, line))
    return lines


@pytest.mark.arch
def test_agent_docs_repo_links_resolve() -> None:
    """Every link or path reference into this repo must name a committed file.

    Covers raw.githubusercontent.com links, relative markdown links, and
    backticked path mentions in llms.txt and AGENTS.md, so a rename or removal
    fails here instead of shipping a dead reference to every agent that reads
    the repo front door.
    """
    violations: list[str] = []
    for doc, lineno, line in _doc_lines():
        referenced = (
            _RAW_LINK_RE.findall(line) + _MD_LINK_RE.findall(line) + _PATH_MENTION_RE.findall(line)
        )
        for rel in referenced:
            target = REPO_ROOT / rel
            if not target.exists():
                violations.append(
                    f"{doc.relative_to(REPO_ROOT)}:{lineno} — references {rel!r}, which does "
                    "not exist in the repo (fix the link or restore the file)"
                )
    assert not violations, "Agent docs reference missing repo paths:\n" + "\n".join(violations)


@pytest.mark.arch
def test_agent_docs_cli_commands_exist() -> None:
    """Every ``jentic``/``jenticctl`` command mentioned must exist in the CLI.

    Validated against ``ui/public/cli-reference.json`` (itself drift-guarded
    against the cobra tree), checking the binary plus first subcommand — deeper
    segments are arguments or subcommands the one-level reference does not
    carry. A renamed or removed command fails here instead of silently leaving
    the front-door docs instructing agents to run something that errors.
    """
    known = _command_paths()
    violations: list[str] = []
    for doc, lineno, line in _doc_lines():
        for mention in _CLI_MENTION_RE.findall(line):
            tokens = mention.split()
            prefix = " ".join(tokens[:2])
            if prefix not in known:
                violations.append(
                    f"{doc.relative_to(REPO_ROOT)}:{lineno} — mentions `{mention}`, but "
                    f"{prefix!r} is not in ui/public/cli-reference.json (command renamed or "
                    "removed? update the doc, or run `make cli-reference` if the reference "
                    "is stale)"
                )
    assert not violations, "Agent docs mention unknown CLI commands:\n" + "\n".join(violations)
