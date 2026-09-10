# Platform support

What runs where, and why. Grounded in what CI actually builds and tests —
anything not listed as tested should be treated as best-effort.

## The matrix

| Component | Linux | macOS | Windows (native) | Windows + WSL2 |
| --------- | ----- | ----- | ---------------- | -------------- |
| Server — Docker ([docker.md](docker.md)) | ✅ Supported | ✅ Supported (Docker Desktop) | ✅ Supported via Docker Desktop (Linux containers) | ✅ Supported |
| Server — Kubernetes/Helm ([helm.md](helm.md)) | ✅ Supported (Linux nodes) | — | — | — |
| Server — from source (`make dev`) | ✅ Supported | ✅ Supported | ❌ Unsupported — POSIX `Makefile` and shell scripts | ✅ Supported (as Linux) |
| `jentic` — agent CLI | ✅ Supported (amd64, arm64) | ✅ Supported (amd64, arm64) | ✅ Supported (amd64; arm64 builds but is untested) | ✅ Supported |
| `jenticctl` — operator CLI | ✅ Supported (amd64, arm64) | ✅ Supported (amd64, arm64) | ❌ Not shipped — its surface is Docker/compose lifecycle; use WSL2 | ✅ Supported |
| [`tools/install.sh`](../../tools/install.sh) installer | ✅ Supported | ✅ Supported | ❌ Unsupported — bash script | ✅ Supported |
| `jentic run` — local-agent confinement | ✅ Supported (`bwrap` + user namespaces) | ✅ Supported (`sandbox-exec`) | ❌ Unsupported — no confinement backend | ❌ Unsupported — needs unprivileged user namespaces inside WSL; untested |
| `jentic mcp` — local MCP stdio server | ✅ Supported | ✅ Supported | ⚠️ Partial — stdio mode works; `--http`'s default unix-socket/OS-identity mode fails closed (peer-credential checks are unsupported on Windows), and `mcp config` cannot locate the Claude Desktop config path | ✅ Supported |

One nuance the Linux column hides: the Go CLIs are static binaries
(`CGO_ENABLED=0`) and run on **musl** distributions like Alpine, but the
server container image is Ubuntu/glibc-based — "Linux ✅" for the server
means a host that can run glibc Linux containers, not that the app runs on
a musl userland directly.

## Windows in practice

Step-by-step path: [windows.md](windows.md). The shape:

- **Running the server:** use Docker Desktop (which runs Linux containers via
  its WSL2 backend) and follow [docker.md](docker.md) unchanged. Building or
  running the server natively is unsupported.
- **Agent-side tooling:** the `jentic` CLI ships natively for Windows as a
  `.zip` containing `jentic.exe` — agents can search, inspect, and execute
  against a broker from native Windows.
- **Operating an install:** `jenticctl` is deliberately not shipped for
  Windows; its job is Docker/compose lifecycle management, which on Windows
  lives in WSL2. Install it inside WSL2 and treat that environment as Linux.
- **Local-agent isolation (`jentic run`):** unavailable — the confinement
  layer requires macOS `sandbox-exec` or Linux `bwrap`.

## What CI actually tests

| Leg | Coverage |
| --- | -------- |
| `ubuntu-latest` | Full test suite (unit, arch, integration) |
| `ubuntu-24.04` | Headless Docker install + live-stack smoke (the e2e matrix leg) |
| `macos-latest` | Build via [`tools/install.sh`](../../tools/install.sh), OS-sensitive Go tests, offline smoke |
| `windows-latest` (amd64) | Binary build, OS-sensitive Go tests, offline smoke (PowerShell) |

Windows arm64 binaries cross-compile in release builds but are not smoke-tested
on any runner. WSL2 is not exercised in CI; it is expected to behave as Linux.

## See also

- [the installation index](README.md) — pick an install path
- [cli.md](cli.md) — CLI download matrix and verification
- [docker.md](docker.md) / [helm.md](helm.md) / [systemd.md](systemd.md) — per-platform installs
