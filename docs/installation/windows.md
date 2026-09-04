# Jentic One on Windows

The server runs in Linux containers; the agent CLI runs natively. So a
Windows setup is two halves: **WSL2 for operating the server, native
`jentic.exe` for the agent side**. Nothing else is Windows-specific — once
WSL2 is in place you follow the Linux instructions unchanged.

## 1. Enable WSL2

In an elevated PowerShell:

```powershell
wsl --install
```

Reboot when prompted; you get Ubuntu by default. (Anything WSL2-capable
works — [Microsoft's guide](https://learn.microsoft.com/windows/wsl/install)
covers older Windows builds.)

## 2. Install Docker Desktop with the WSL2 backend

Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
and leave **"Use the WSL 2 based engine"** on (the default). Under
*Settings → Resources → WSL integration*, enable your distro so `docker`
works inside it.

## 3. Run the server — inside WSL2, as Linux

Open a WSL shell (`wsl` from any terminal) and follow the
[README quickstart](../../README.md#quickstart) or the
[Docker guide](docker.md) **unchanged** — the snippets are POSIX shell and
run as-is. Two things Docker Desktop does for you:

- `127.0.0.1` port publishes are forwarded to the Windows host's loopback,
  so `http://127.0.0.1:8000` works in your Windows browser.
- `jenticctl` (the operator CLI) installs inside WSL2 like on Linux — it is
  deliberately not shipped for native Windows, because its job is
  Docker/compose lifecycle, which lives in WSL2 here.

## 4. The agent side — native Windows

`jentic.exe` ships natively (amd64 + arm64):

```powershell
winget install Jentic.Jentic

# or, via our Scoop bucket (carries brand-new releases before winget review completes):
scoop bucket add jentic https://github.com/jentic/scoop-bucket
scoop install jentic
```

`winget` resolves the package only after Microsoft accepts the manifest
submission for a release — "No package found matching input criteria" means
use the Scoop bucket above instead.

Manual `.zip` download and cosign verification are in [cli.md](cli.md).
Agents on native Windows can `register`, `search`,
`inspect`, and `execute` against the broker at `http://127.0.0.1:8100`
thanks to the loopback forwarding above — no WSL needed on the agent side.

## Caveats

- **`jentic run` (local-agent confinement) is unavailable** on Windows —
  the confinement layer needs macOS `sandbox-exec` or Linux `bwrap`. Run
  coding agents on a separate machine from the instance, or accept the
  [same-host risks](../security/same-host/README.md).
- Windows **arm64** binaries are built but not smoke-tested in CI.
- The full support matrix, including what CI actually tests, is in
  [platform-support.md](platform-support.md).
