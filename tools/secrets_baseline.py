"""Keep ``.secrets.baseline`` in slim form and gate new secrets against it.

detect-secrets matches a baseline entry on ``(filename, hashed_secret, type)``;
the stored ``line_number`` and the top-level ``generated_at`` timestamp play no
part in detection. They do, however, change whenever a file holding a known
test secret is edited or the baseline is regenerated, which turns the baseline
into a merge-conflict magnet. This tool stores the baseline without either
field, sorted deterministically, so it only changes when the *set* of
allowlisted findings changes.

Upstream's ``--slim`` flag only applies to a fresh scan printed to stdout:
``detect-secrets scan --baseline`` and ``detect-secrets-hook`` both write the
full form back. Every write therefore goes through this wrapper, which
normalizes the file afterwards.

Subcommands (``uv run python -m tools.secrets_baseline <cmd>``):

- ``update`` (``make secrets-baseline``): re-scan the repo into the baseline,
  then normalize it. Run this after adding or removing a test secret, or to
  resolve a merge conflict on the baseline (take either side, then update).
- ``hook <files...>`` (lefthook pre-commit): run ``detect-secrets-hook`` on the
  staged files. A new secret fails the commit; if the hook rewrites the
  baseline (a known secret was removed), the file is normalized and the commit
  fails with exit 3 so the change can be staged.
- ``check`` (``make detect-secrets``, CI): re-scan without leaving changes
  behind and fail if the result differs from the committed baseline: a new
  unallowlisted secret, a stale entry, or a baseline that is not normalized.

Slim baselines do not support ``detect-secrets audit`` (it needs line
numbers). To audit, run a throwaway full scan into a scratch file.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = ".secrets.baseline"
BASELINE = REPO_ROOT / BASELINE_NAME

#: Matches the historical ``make detect-secrets`` invocation.
SCAN_ARGS: tuple[str, ...] = ("--exclude-files", r"\.git/")

#: Per-entry fields that do not take part in baseline matching.
_VOLATILE_ENTRY_KEYS = ("line_number",)
#: Top-level fields that do not take part in baseline matching.
_VOLATILE_TOP_KEYS = ("generated_at",)

_FIX_HINT = "run `make secrets-baseline` and commit the result"


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Return the slim, deterministically ordered form of a baseline document.

    Drops ``generated_at`` and every ``line_number``, sorts files by name and
    each file's entries by ``(hashed_secret, type)`` (detect-secrets' own order
    when no line numbers are tracked). All other fields are kept verbatim.
    """
    out = {k: v for k, v in data.items() if k not in _VOLATILE_TOP_KEYS}
    results: dict[str, list[dict[str, Any]]] = {}
    for filename in sorted(data.get("results", {})):
        entries = [
            {k: v for k, v in entry.items() if k not in _VOLATILE_ENTRY_KEYS}
            for entry in data["results"][filename]
        ]
        entries.sort(key=lambda e: (e["hashed_secret"], e["type"]))
        results[filename] = entries
    out["results"] = results
    return out


def render(data: dict[str, Any]) -> str:
    """Serialize a baseline the way detect-secrets does (2-space JSON + newline)."""
    return json.dumps(data, indent=2) + "\n"


def normalize_text(text: str) -> str:
    """Normalize a serialized baseline."""
    return render(normalize(json.loads(text)))


def finding_keys(data: dict[str, Any]) -> set[tuple[str, str, str]]:
    """The ``(filename, type, hashed_secret)`` identities a baseline allowlists."""
    return {
        (filename, entry["type"], entry["hashed_secret"])
        for filename, entries in data.get("results", {}).items()
        for entry in entries
    }


def compare(committed_text: str, scanned_text: str) -> list[str]:
    """Explain how a committed baseline differs from a fresh scan's baseline.

    Returns one human-readable problem per line; an empty list means the
    committed file is exactly the normalized scan result.
    """
    committed = json.loads(committed_text)
    scanned = normalize(json.loads(scanned_text))
    problems: list[str] = []

    committed_keys = finding_keys(committed)
    scanned_keys = finding_keys(scanned)
    for filename, kind, _hash in sorted(scanned_keys - committed_keys):
        problems.append(f"potential secret not in baseline: {filename} ({kind})")
    for filename, kind, _hash in sorted(committed_keys - scanned_keys):
        problems.append(f"stale baseline entry (no longer found): {filename} ({kind})")

    if not problems and committed_text != render(scanned):
        problems.append(
            f"{BASELINE_NAME} is not in normalized slim form "
            "(line_number/generated_at present, or entries out of order)"
        )
    return problems


def _normalize_file(path: Path) -> None:
    path.write_text(normalize_text(path.read_text(encoding="utf-8")), encoding="utf-8")


def _run_scan() -> int:
    """Re-scan the repo into ``BASELINE`` in place (detect-secrets' full form)."""
    cmd = [sys.executable, "-m", "detect_secrets", "scan", "--baseline", BASELINE_NAME, *SCAN_ARGS]
    return subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode


def cmd_update() -> int:
    rc = _run_scan()
    if rc != 0:
        return rc
    _normalize_file(BASELINE)
    print(f"secrets_baseline: updated {BASELINE_NAME} (slim form)")
    return 0


def cmd_hook(files: list[str]) -> int:
    if not files:
        return 0
    cmd = [
        sys.executable,
        "-m",
        "detect_secrets.pre_commit_hook",
        "--baseline",
        BASELINE_NAME,
        *files,
    ]
    rc = subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode
    if rc == 3:
        # The hook rewrote the baseline in full form; keep it slim.
        _normalize_file(BASELINE)
    return rc


def cmd_check() -> int:
    original = BASELINE.read_text(encoding="utf-8")
    try:
        rc = _run_scan()
        scanned = BASELINE.read_text(encoding="utf-8")
    finally:
        BASELINE.write_text(original, encoding="utf-8")
    if rc != 0:
        print(f"secrets_baseline: detect-secrets scan failed (exit {rc})", file=sys.stderr)
        return rc

    problems = compare(original, scanned)
    if problems:
        for p in problems:
            print(f"secrets_baseline: {p}", file=sys.stderr)
        print(
            "secrets_baseline: a real secret must be removed from the change; a test "
            f"fixture can be marked `pragma: allowlist secret` inline, or {_FIX_HINT}",
            file=sys.stderr,
        )
        return 1
    print(f"secrets_baseline: {BASELINE_NAME} matches a fresh scan")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update", help="re-scan into the baseline and normalize it")
    hook = sub.add_parser("hook", help="pre-commit hook over the given files")
    hook.add_argument("files", nargs="*")
    sub.add_parser("check", help="fail if the baseline differs from a fresh scan")
    args = parser.parse_args(argv)

    if args.cmd == "update":
        return cmd_update()
    if args.cmd == "hook":
        return cmd_hook(args.files)
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
