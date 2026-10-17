#!/usr/bin/env bash
# Fail when a built image's Python packages differ from uv.lock (#1819).
#
# Usage: scripts/check-image-lock.sh <image> [docker-run args, e.g. --platform linux/arm64]
#
# The expected set is `uv export --frozen --no-dev` of this checkout. Its
# environment markers are evaluated by the image's own interpreter, so a
# package gated on another platform (e.g. `sys_platform == 'win32'`) is
# expected only where it applies. The comparison is exact: a missing,
# extra, or differently versioned distribution fails. `pip` (seeded by
# ensurepip) and the project itself (installed --no-deps from the wheel) are
# outside the lock export; the project must still be present. The in-image
# checker uses `packaging`, a locked runtime dependency of the image.
set -euo pipefail

image="${1:?usage: $0 <image> [docker-run args]}"
shift

read -r -d '' checker <<'PY' || true
import re
import sys
from importlib.metadata import distributions

from packaging.markers import Marker
from packaging.version import Version

PROJECT = "jentic-one"
NOT_LOCKED = {"pip", PROJECT}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


expected: dict[str, str] = {}
for raw in sys.stdin:
    line = raw.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    spec, _, marker = line.partition(";")
    if marker.strip() and not Marker(marker.strip()).evaluate():
        continue
    name, sep, version = spec.partition("==")
    if not sep:
        sys.exit(f"unpinned requirement in lock export: {line!r}")
    expected[norm(name.split("[", 1)[0].strip())] = version.strip()

installed = {norm(d.metadata["Name"]): d.version for d in distributions()}
problems = []
if PROJECT not in installed:
    problems.append(f"missing  {PROJECT} (the project wheel)")
for name in sorted(expected.keys() - installed.keys()):
    problems.append(f"missing  {name}=={expected[name]}")
for name in sorted(installed.keys() - expected.keys() - NOT_LOCKED):
    problems.append(f"extra    {name}=={installed[name]}")
for name in sorted(expected.keys() & installed.keys()):
    if Version(expected[name]) != Version(installed[name]):
        problems.append(f"differs  {name}: lock {expected[name]}, image {installed[name]}")

if problems:
    print(f"image packages differ from uv.lock ({len(problems)}):", *problems, sep="\n  ")
    sys.exit(1)
print(f"image matches uv.lock: {len(expected)} locked packages")
PY

uv export --frozen --no-dev --no-emit-project --no-hashes --quiet \
    | docker run --rm -i "$@" --entrypoint python "${image}" -c "${checker}"
