"""Tests for ``.harness/setup.sh``, the automated worker's setup entry point, and ``make ready``.

The script is run for real with ``make``, ``uv``, ``mise`` and ``git`` replaced
by stubs on ``PATH`` (the out-of-process boundary), so no dependency install,
fixture or network call happens.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SETUP = REPO_ROOT / ".harness" / "setup.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(
    tmp_path: Path, *, make_exit: int, extra_env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _stub(bin_dir, "make", f'echo "make $*" >>"{calls}"; exit {make_exit}')
    _stub(bin_dir, "uv", "exit 0")
    _stub(bin_dir, "git", f'echo "git $*" >>"{calls}"; mkdir -p "${{@: -1}}"')
    workdir = tmp_path / "repo"
    workdir.mkdir()
    env_file = tmp_path / "agent-env"
    env_file.touch()
    env = {
        # No mise on PATH: the script runs the stubs directly.
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HARNESS_ENV_FILE": str(env_file),
        **(extra_env or {}),
    }
    result = subprocess.run(
        ["bash", str(SETUP)],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, calls


def test_setup_runs_make_ready_and_succeeds(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, make_exit=0)
    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["make ready"]
    assert result.stdout.strip().endswith("ready")


def test_setup_fails_when_make_ready_fails(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, make_exit=1, extra_env={"JENTIC_RULES_REPO": "acme/rules"})
    assert result.returncode != 0
    # Nothing runs after the failed readiness step.
    assert calls.read_text(encoding="utf-8").splitlines() == ["make ready"]
    assert "ready" not in result.stdout.split()


def test_setup_fetches_rules_only_on_request_over_a_plain_url(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, make_exit=0, extra_env={"JENTIC_RULES_REPO": "acme/rules"})
    assert result.returncode == 0, result.stderr
    clone = calls.read_text(encoding="utf-8").splitlines()[1]
    assert clone.startswith("git clone ")
    assert "https://github.com/acme/rules.git" in clone
    assert "@github.com" not in clone and "extraheader" not in clone
    env_file = (tmp_path / "agent-env").read_text(encoding="utf-8")
    assert env_file == f"JENTIC_RULES_DIR={tmp_path / 'repo' / '.rules'}\n"


@pytest.mark.parametrize("repo", ["acme", "acme/rules;rm -rf /", "https://x@github.com/a/b"])
def test_setup_rejects_a_malformed_rules_repo(tmp_path: Path, repo: str) -> None:
    result, calls = _run(tmp_path, make_exit=0, extra_env={"JENTIC_RULES_REPO": repo})
    assert result.returncode != 0
    assert "git" not in calls.read_text(encoding="utf-8")
