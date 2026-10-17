"""Every image that installs the project wheel must install exactly ``uv.lock``.

``scripts/check-image-lock.sh`` checks a built image against the lock, but CI
only builds and checks the combined ``app`` image. The per-service images
(``admin``, ``broker``, ``control``, ``registry``, used by Helm parts mode)
and the multi-arch release ``app`` stage carry their own copy of the install
step, so this gate pins the properties the guard relies on in every
Dockerfile (#1819):

* the locked requirements install with ``--require-hashes`` and
  ``--only-binary=:all:`` (no sdist builds, whose build backends pip fetches
  without hash checking);
* the wheel installs with ``--no-deps`` (pip never re-resolves the wheel's
  open ``pyproject.toml`` ranges), followed by ``pip check``;
* no other ``pip install`` runs in a service image;
* the builder's lock export uses ``--locked`` and honours the ``EXTRAS``
  build arg;
* the uv and Python builder images match ``python-base.Dockerfile``, which is
  where the guard reads them from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
BASE_DOCKERFILE = DOCKER_DIR / "python-base.Dockerfile"
GUARD_SCRIPT = REPO_ROOT / "scripts" / "check-image-lock.sh"

#: Dockerfiles that install the project wheel. Listed explicitly so a new
#: service image (or a renamed one) fails here until it is covered.
WHEEL_INSTALLERS = {
    "admin.Dockerfile",
    "app.Dockerfile",
    "app.multiarch.Dockerfile",
    "broker.Dockerfile",
    "control.Dockerfile",
    "registry.Dockerfile",
}

#: Dockerfiles whose builder stage writes the lock export.
LOCK_EXPORTERS = {"python-base.Dockerfile", "app.multiarch.Dockerfile"}

_UV_IMAGE_RE = re.compile(r"^COPY --from=(ghcr\.io/astral-sh/uv:\S+) ", re.MULTILINE)
_PYTHON_IMAGE_RE = re.compile(r"^ARG PYTHON_IMAGE=(\S+)$", re.MULTILINE)


def _instructions(path: Path) -> list[str]:
    """Dockerfile instructions with ``\\`` continuations joined, comments dropped."""
    joined: list[str] = []
    current = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        joined.append(current + line)
        current = ""
    if current:
        joined.append(current)
    return joined


def _shell_commands(path: Path) -> list[str]:
    """Every ``&&``-separated command of every ``RUN`` instruction."""
    commands: list[str] = []
    for instruction in _instructions(path):
        if instruction.startswith("RUN "):
            commands.extend(part.strip() for part in instruction[4:].split("&&"))
    return commands


def _pip_install_violations(path: Path) -> list[str]:
    name = path.name
    commands = _shell_commands(path)
    violations: list[str] = []
    locked = wheel = check_after_wheel = False
    for command in commands:
        if "pip install" in command:
            if "-r /tmp/requirements.txt" in command:
                locked = True
                for flag in ("--require-hashes", "--only-binary=:all:"):
                    if flag not in command:
                        violations.append(f"{name}: locked requirements install lacks {flag}")
            elif "jentic_one-" in command or '"${whl}"' in command:
                wheel = True
                if "--no-deps" not in command:
                    violations.append(f"{name}: wheel install lacks --no-deps")
            else:
                violations.append(
                    f"{name}: unexpected `{command}` (only the locked requirements and "
                    "the --no-deps wheel install may run; anything else re-resolves "
                    "against PyPI)"
                )
        elif wheel and command.endswith("pip check"):
            check_after_wheel = True
    if not locked:
        violations.append(f"{name}: no `pip install --require-hashes -r /tmp/requirements.txt`")
    if not wheel:
        violations.append(f"{name}: no --no-deps wheel install")
    if not check_after_wheel:
        violations.append(f"{name}: no `pip check` after the wheel install")
    return violations


@pytest.mark.arch
def test_wheel_installers_are_the_expected_set() -> None:
    """Every Dockerfile that installs the wheel is in ``WHEEL_INSTALLERS``."""
    found = {
        path.name
        for path in DOCKER_DIR.glob("*.Dockerfile")
        if any("pip install" in command for command in _shell_commands(path))
    }
    assert found == WHEEL_INSTALLERS, (
        f"Dockerfiles running pip install: {sorted(found)}; expected "
        f"{sorted(WHEEL_INSTALLERS)}. Cover a new image here and give it the "
        "locked install step"
    )


@pytest.mark.arch
@pytest.mark.parametrize("name", sorted(WHEEL_INSTALLERS))
def test_image_installs_exactly_the_lock(name: str) -> None:
    """The install step keeps the image on the hash-pinned lock export."""
    violations = _pip_install_violations(DOCKER_DIR / name)
    assert not violations, "\n".join(violations)


@pytest.mark.arch
@pytest.mark.parametrize("name", sorted(LOCK_EXPORTERS))
def test_lock_export_is_locked_and_honours_extras(name: str) -> None:
    """The builder export fails on a stale lock and carries ``EXTRAS``."""
    path = DOCKER_DIR / name
    exports = [command for command in _shell_commands(path) if "uv export" in command]
    assert len(exports) == 1, f"{name}: expected one `uv export`, found {exports}"
    export = exports[0]
    assert "--locked" in export, f"{name}: uv export must use --locked"
    assert "--frozen" not in export, f"{name}: --frozen skips the pyproject.toml check"
    assert "${EXTRAS}" in export and "--extra" in export, (
        f"{name}: uv export must forward the EXTRAS build arg as --extra flags"
    )
    assert any(i == 'ARG EXTRAS=""' for i in _instructions(path)), (
        f'{name}: the builder stage must declare ARG EXTRAS=""'
    )


@pytest.mark.arch
def test_builder_images_match_the_guard() -> None:
    """The multi-arch builder uses the uv and Python images the guard reads."""
    base = BASE_DOCKERFILE.read_text(encoding="utf-8")
    base_uv = _UV_IMAGE_RE.findall(base)
    base_python = _PYTHON_IMAGE_RE.findall(base)
    assert len(base_uv) == 1 and len(base_python) == 1, (
        "python-base.Dockerfile must have exactly one uv COPY --from and one "
        "ARG PYTHON_IMAGE (scripts/check-image-lock.sh reads both)"
    )
    for name in sorted(LOCK_EXPORTERS - {BASE_DOCKERFILE.name}):
        text = (DOCKER_DIR / name).read_text(encoding="utf-8")
        assert _UV_IMAGE_RE.findall(text) == base_uv, f"{name}: uv image differs"
        assert _PYTHON_IMAGE_RE.findall(text) == base_python, f"{name}: PYTHON_IMAGE differs"

    guard = GUARD_SCRIPT.read_text(encoding="utf-8")
    assert "python-base.Dockerfile" in guard and "uv export --locked" in guard, (
        "scripts/check-image-lock.sh must export with --locked using the uv and "
        "Python images from python-base.Dockerfile"
    )
