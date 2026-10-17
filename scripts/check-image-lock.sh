#!/usr/bin/env bash
# Fail when a built image's Python packages differ from uv.lock (#1819).
#
# Usage: scripts/check-image-lock.sh [--extra <name>]... [--project-version <v>] \
#            <image> [docker-run args, e.g. --platform linux/arm64]
#
# The expected set is `uv export --locked --no-dev` of this checkout, plus one
# `--extra` per extra the image was built with (its EXTRAS build arg). The
# export runs in the builder stage's own Python image with the builder's own
# uv, both read from deploy/docker/python-base.Dockerfile, so the guard and
# the image never disagree on how the lock is read, and the host needs no uv.
# --locked fails when uv.lock is out of date with pyproject.toml.
#
# Environment markers are evaluated by the image's own interpreter, so a
# package gated on another platform (e.g. `sys_platform == 'win32'`) is
# expected only where it applies. The comparison is exact: a missing, extra,
# differently versioned, duplicated or unnamed distribution fails. `pip`
# (seeded by ensurepip) and the project itself (installed --no-deps from the
# wheel) are outside the lock export; the project must still be installed, at
# --project-version (default: the version in pyproject.toml).
#
# The in-image checker uses `packaging`, which the image carries only as a
# transitive dependency (via opentelemetry-instrumentation); the checker exits
# with a clear message if it is ever missing.
set -euo pipefail

usage() {
    echo "usage: $0 [--extra <name>]... [--project-version <v>] <image> [docker-run args]" >&2
    exit 2
}

extras=()
project_version=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --extra)
            [[ $# -ge 2 ]] || usage
            extras+=(--extra "$2")
            shift 2
            ;;
        --project-version)
            [[ $# -ge 2 ]] || usage
            project_version="$2"
            shift 2
            ;;
        -*) usage ;;
        *) break ;;
    esac
done
[[ $# -ge 1 ]] || usage
image="$1"
shift

repo="$(cd "$(dirname "$0")/.." && pwd)"
base_dockerfile="${repo}/deploy/docker/python-base.Dockerfile"
python_image="$(sed -n 's/^ARG PYTHON_IMAGE=//p' "${base_dockerfile}")"
uv_image="$(sed -n 's|^COPY --from=\(ghcr\.io/astral-sh/uv:[^ ]*\) .*|\1|p' "${base_dockerfile}")"
if [[ -z "${python_image}" || -z "${uv_image}" ]]; then
    echo "cannot read PYTHON_IMAGE / the uv image from ${base_dockerfile}" >&2
    exit 2
fi

tmp="$(mktemp -d)"
uv_container=""
cleanup() {
    [[ -z "${uv_container}" ]] || docker rm -f "${uv_container}" >/dev/null 2>&1 || true
    rm -rf "${tmp}"
}
trap cleanup EXIT

# The export helpers run on the daemon's native platform. Both are pinned to
# it so a cached foreign-arch uv or Python tag (e.g. a `docker pull --platform`
# on another arch) is re-resolved instead of failing with an exec format error;
# the lock export is platform-independent, so native is correct for every
# target image.
native_platform="linux/$(docker version -f '{{.Server.Arch}}')"

# The uv image is FROM scratch (no interpreter for uv to discover), so its
# binary runs inside the builder's Python image instead, as in the builder.
uv_container="$(docker create --platform "${native_platform}" "${uv_image}")"
docker cp -q "${uv_container}:/uv" "${tmp}/uv"

in_builder() {
    docker run --rm --platform "${native_platform}" -e UV_NO_CACHE=1 \
        -v "${tmp}/uv:/usr/local/bin/uv:ro" -v "${repo}:/src:ro" -w /src \
        "${python_image}" "$@"
}

if [[ -z "${project_version}" ]]; then
    project_version="$(in_builder python -c \
        "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"
fi

if ! in_builder uv export --locked --offline --no-dev --no-emit-project --no-hashes --quiet \
    ${extras[@]+"${extras[@]}"} >"${tmp}/expected.txt"; then
    # Offline, a stale lock surfaces as an unsatisfiable resolution rather
    # than uv's "needs to be updated" message.
    echo "check-image-lock: \`uv export --locked\` failed; uv.lock is likely out of" \
        "date with pyproject.toml (run \`make lock\`), or an --extra is unknown" >&2
    exit 1
fi

read -r -d '' checker <<'PY' || true
import re
import sys
from importlib.metadata import distributions

try:
    from packaging.markers import Marker
    from packaging.version import Version
except ImportError:
    sys.exit(
        "check-image-lock: `packaging` is not installed in the image; the guard "
        "relies on it arriving transitively (via opentelemetry-instrumentation)"
    )

PROJECT = "jentic-one"
NOT_LOCKED = {"pip", PROJECT}
project_version = sys.argv[1]


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

problems: list[str] = []
installed: dict[str, list[tuple[str, str]]] = {}
for dist in distributions():
    where = str(getattr(dist, "_path", "?"))
    name = dist.metadata.get("Name")
    if not name or not dist.version:
        problems.append(f"broken   distribution without Name/Version at {where}")
        continue
    installed.setdefault(norm(name), []).append((dist.version, where))

for name, found in sorted(installed.items()):
    if len(found) > 1:
        listed = ", ".join(f"{version} ({where})" for version, where in found)
        problems.append(f"duplicate {name}: {listed}")
versions = {name: found[0][0] for name, found in installed.items()}

if PROJECT not in versions:
    problems.append(f"missing  {PROJECT}=={project_version} (the project wheel)")
elif Version(versions[PROJECT]) != Version(project_version):
    problems.append(
        f"differs  {PROJECT}: pyproject {project_version}, image {versions[PROJECT]}"
    )
for name in sorted(expected.keys() - versions.keys()):
    problems.append(f"missing  {name}=={expected[name]}")
for name in sorted(versions.keys() - expected.keys() - NOT_LOCKED):
    problems.append(f"extra    {name}=={versions[name]}")
for name in sorted(expected.keys() & versions.keys()):
    if Version(expected[name]) != Version(versions[name]):
        problems.append(f"differs  {name}: lock {expected[name]}, image {versions[name]}")

if problems:
    print(f"image packages differ from uv.lock ({len(problems)}):", *problems, sep="\n  ")
    sys.exit(1)
print(f"image matches uv.lock: {len(expected)} locked packages, {PROJECT} {project_version}")
PY

docker run --rm -i "$@" --entrypoint python "${image}" -c "${checker}" "${project_version}" \
    <"${tmp}/expected.txt"
