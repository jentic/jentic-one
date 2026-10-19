# Spike 1 — can the CLI sandbox run under the chart's pod posture?

Date 2026-10-19. Host: Docker Desktop 28.3.2, linuxkit kernel 6.10.14, runc 1.2.5
(arm64). gVisor (`runsc`) is not installed here, so it is **not tested**.
Image: `Dockerfile` (debian bookworm-slim, `gh` 2.89.0 sha256-verified,
bubblewrap 0.8). Probes: `probe.sh`, `probe2.sh`.

## Results

| Posture | User/net namespace | bwrap + `gh` |
|---|---|---|
| A. Docker defaults (default seccomp + default caps, non-root) | ❌ `unshare: Operation not permitted` | ❌ |
| B. **Chart posture** (`drop: ALL`, no-new-privs, read-only rootfs, RuntimeDefault seccomp, uid 10001), as `deploy/helm/jentic-one/charts/broker/values.yaml:46-53` | ❌ | ❌ |
| C. B + `seccomp=unconfined` | ✅ | ✅ only without a fresh `/proc` mount (`Can't mount proc … Operation not permitted`) |
| D. B + `CAP_SYS_ADMIN` | ✅ (`unshare -Urn`) | ❌ `pivot_root: Operation not permitted` |
| E/F. C + `apparmor=unconfined` (± no-new-privs) | ✅ | same as C |
| **G. B + custom seccomp** = Docker default + allow `unshare, clone, clone3, mount, umount2, pivot_root, setns` (`seccomp-cli-runner.json`) | ✅ | ✅ (no fresh `/proc`) |

What G gives you, with all capabilities still dropped, no-new-privs and a
read-only rootfs:

- **Network is isolated.** `curl` inside the sandbox can't even resolve DNS.
- **The env is clean.** A secret env var on the container isn't visible inside.
- **PID namespace works**, without a fresh `/proc` mount.
- **Overhead is negligible:** `gh --version` takes 15 ms sandboxed vs 13–14 ms
  bare (mean of 20 runs).

## What this means

1. **The runner can't use the broker's pod posture as-is.** It needs one
   change: a **Localhost seccomp profile** (G). It does *not* need
   capabilities, privileged mode or AppArmor changes. That makes it a
   separate workload with its own `securityContext`.
2. **No fresh `/proc` inside the sandbox.** Docker/runc masks `/proc`
   sub-paths, which blocks a new procfs mount. `gh` doesn't need it. Every
   other CLI must be checked (`aws` is Python, `cf` is Node).
3. **Deployment matrix, inferred and not yet tested:**
   - **Compose:** works via `security_opt: ["seccomp=…json"]`.
   - **Self-managed k8s:** works via `seccompProfile: {type: Localhost}`, but
     the profile file has to be on every node (a DaemonSet or the
     security-profiles-operator).
   - **Managed k8s that forbids Localhost profiles** (GKE Autopilot, EKS
     Fargate, PSS-restricted namespaces): doesn't work. It needs a gVisor
     RuntimeClass or k8s user namespaces (`hostUsers: false`), and both are
     untested.
   - **Ubuntu ≥ 23.10 hosts** default to
     `kernel.apparmor_restrict_unprivileged_userns=1`, which may block this
     even with the seccomp profile. Untested; this is the next thing to check.
   - **macOS dev:** works through Docker Desktop, which is what this spike
     ran on.
4. **Fail-closed is easy to detect.** A profile probe at runner startup (as
   in `probe.sh`) can tell the difference between "no namespaces" and
   "sandbox OK", and mark the profile unavailable.

## Addendum — `aws` startup penalty and `/proc` (2026-10-19)

`aws --version` costs ~250 ms more inside the sandbox (501 vs 247 ms). Ablation
(`ablate.sh`) isolates the cause:

| Variant | ms |
|---|---|
| bare | 247 |
| any bwrap variant without `/proc` (user only, user+net, user+pid, full) | ~502 |
| user+net + `--ro-bind /proc /proc` | 248 |
| HOME on real `/tmp` | 503 |

The cause is **the missing `/proc`, not the namespaces.** It's consistent with
the CLI's bundled launcher (`aws` is a frozen Python app) falling back slowly
when it can't read `/proc` (e.g. `/proc/self/exe`). This is inferred, not
traced.

Options (`freshproc.sh`):

- **Bind the outer `/proc` read-only.** Fast, but each sandbox can then see
  the outer container's processes, including other concurrent invocations'
  argv.
- **Fresh `/proc` in the sandbox's PID namespace.** Fails under the default
  container `/proc` masking. It **works with `--security-opt
  systempaths=unconfined`**, where the sandbox sees only its own PIDs (1–5)
  and `aws` runs at bare speed (274 vs 274 ms). The cost is that the outer
  container's `/proc` is unmasked. The k8s equivalent is
  `securityContext.procMount: Unmasked`, which requires user-namespaced pods
  (`hostUsers: false`). Not tested on k8s.
- **Accept ~250 ms per `aws` invocation.** `gh` and `cf` are unaffected
  (Go/Node).
