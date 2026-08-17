# Plan — confine the agent process with `sandbox-exec` / `bwrap`

> **Implemented.** This began as the design for a per-session process-confinement
> layer on top of the agent-account + allow-ACL model, replacing the `chmod 700 ~`
> default-deny. It now ships in `cli/internal/localagent/confine.go`; this doc is
> kept as the reference for *why* the profile is shaped the way it is. The mechanics
> it builds on live in [`filesystem-access-model.md`](filesystem-access-model.md);
> the flows it slots into live in [`local-agent-isolation.md`](local-agent-isolation.md).
> Two boundaries hardened since the first draft: the deny covers **all** human-home
> roots (`/Users`, `/home`), not just the operator's home, and the agent's
> executable/CLI routes are mounted **read-only** — see
> [Non-negotiable boundaries](filesystem-access-model.md#non-negotiable-boundaries).

## Why

The [sibling-traversal residual](filesystem-access-model.md#the-sibling-traversal-residual-closed-by-confinement)
cannot be closed portably by ACLs/modes alone: POSIX ACLs (Linux) have no `deny`
primitive, and macOS has no native bind mount. The
[settled division of labour](filesystem-access-model.md#chosen-division-of-labour--account--allow-acl--sandbox)
moves per-entry control onto **process confinement** — restrict what the *launched
agent process* can see, rather than stamping the tree. Native on both OSes,
unprivileged, writes nothing to disk, self-cleans on process exit.

## The three layers after this change

1. **Agent Unix account (floor, unchanged).** The credential boundary rests on
   ownership — unconditional, independent of any profile being correct or
   `sandbox-exec` surviving a future macOS. Kept exactly as-is.
2. **Allow-only ACL grants (unchanged).** Traverse-walk + recursive rwx-leaf open
   the one chosen path to the agent uid. Still required — the sandbox is
   **intersection-only** and can never grant a uid access its ownership denies. We
   add **no** default-deny / inherited-deny / per-sibling deny ACEs.
3. **Per-session confinement profile (new).** A Seatbelt profile (macOS) / a
   `bwrap` namespace (Linux) that **denies every human-home root (`/Users`,
   `/home`) except the granted subpaths and the agent's own home**, and marks the
   agent's executable routes **read-only**. This is what closes the sibling leak.
   See [Profile model](#profile-model--targeted-home-deny-not-deny-default) for why
   this is a *targeted deny* rather than a strict `(deny default)` allow-list.

## What changes in code

Small, additive. The ACL machinery is untouched.

| # | Change | File(s) |
|---|--------|---------|
| 1 | **Stop enforcing `chmod 700 ~`.** Remove the `LockOperatorHomeCmd` call from setup. Keep the function only if still referenced; otherwise delete it and its test. | `cli/internal/cmd/agentuser.go`, `cli/internal/localagent/localagent.go` |
| 2 | **Confinement launcher shim.** New `ConfineLaunchCmd(...)` (or wrap `LaunchCmd`) that runs the agent under `sandbox-exec -f <profile>` (macOS) / `bwrap ...` (Linux). When confinement is **unavailable** it does **not** fall back to an unconfined run — it **errors closed** (see below). | `cli/internal/localagent/` (new file, e.g. `confine.go` + `confine_darwin.go` / `confine_linux.go`) |
| 3 | **Profile generation.** Build the deny-set (operator home) and allow-set (granted dirs + runtime needs) from `cfg.GrantedDirs`. Written to a temp file owned/readable by the agent, removed on exit. | same package |
| 4 | **Wire into `jentic run`.** Launch through the shim instead of `LaunchCmd` directly. | `cli/internal/cmd/run.go` |
| 5 | **Availability detection + error-closed.** Detect `sandbox-exec` (macOS) / unprivileged userns + `bwrap` (Linux); on absence, **refuse the launch** with a clear message that fully locked-down sessions aren't available on this machine and point the operator at an alternative (e.g. Dockerising the agent). Never launch unconfined. | shim |

**Config:** `GrantedDirs` already persists the allow-set (`config/file.go`) — the
profile is derived from it, nothing new to store.

## Profile model — targeted home-deny, not `(deny default)`

The profile is **default-allow with a targeted deny on the human-home roots**, not
a strict `(deny default)` allow-list:

```scheme
(version 1)
(allow default)
; close ALL human homes wholesale, then re-open only the granted subpaths
(deny file* (subpath "/Users"))
(deny file* (subpath "/home"))
(allow file-read-metadata (literal "/Users") (literal "/Users/Shared"))  ; ancestor traversal
(allow file* (subpath "/Users/Shared/alice-local-agent"))  ; agent home (first)
(allow file* (subpath "/Users/alice/projects/api"))        ; a granted dir
; executable routes stay read-only so the agent can't rewrite its own launcher
(deny file-write* (subpath "/opt/homebrew/bin"))
(deny file-write* (subpath "/usr/local/bin"))
```

Note the whole-`/Users` (and `/home`) deny, not just the operator's own home: a
grant into one user's tree can never expose another's. The agent home is re-opened
even though it sits under the denied `/Users` root, and its `/Users`, `/Users/Shared`
ancestors get `file-read-metadata` so the kernel can traverse down to it.

**Why not `(deny default)`.** A strict deny-everything-then-allow profile is the
theoretically strongest confinement, but it is **brittle** for a Node-based agent
like `claude`: it must enumerate every runtime dependency the process touches —
dyld and the dylib cache, `/dev`, tmp, sysctls, mach-lookups, network, the shared
Homebrew toolchain — and a single missing rule means the agent **fails to even
start**. That fragility is also version-sensitive: an OS update or an agent update
that reaches for a new path silently breaks the launch. Maintaining that allow-list
across every supported agent and macOS version is a standing cost with a high
false-negative (won't-launch) rate.

**Why targeted home-deny is the right scope.** The objective this layer exists to
meet is precise and narrow: *close the sibling-traversal leak* — grant `~/a`
without implicitly exposing world-readable `~/b`. A `(deny file* (subpath <home>))`
with the granted subpaths re-allowed does exactly that and nothing more, so it is
robust (the agent's own runtime dependencies outside `~` are untouched) and
unlikely to break on an OS/agent update. The residual it accepts is that the agent
process can still reach world-readable material **outside** the operator's home —
but that is already governed by the agent account's ordinary Unix DAC (its own uid,
groups, and the file modes), which is the credential-boundary floor we are *not*
relying on the sandbox to enforce. The sandbox's job here is solely the in-`~`
per-entry distinction that DAC cannot express; it is not trying to be a general
egress jail.

Net: the sandbox is scoped to the one thing it uniquely can do (per-entry control
inside `~`), the agent account continues to do everything it already does
(ownership-based credential isolation), and we avoid the brittleness that would make
locked-down sessions unusable in practice.

## The rule-set (what the profile denies and re-allows)

Because the base is `(allow default)`, the profile only needs to name the human-home
roots (to deny them) and the paths that must be re-opened **inside** them:

- **Deny** — every human-home root (`subpath "/Users"`, `subpath "/home"`).
  Everything under them becomes unreachable to the agent process, regardless of
  which user owns it.
- **Re-allow — the agent's own home** (macOS: under `/Users/Shared/…`, itself under
  the denied `/Users`), plus **the granted dirs**. The first-match / most-specific
  `allow` overrides the root `deny`. Ancestors of each re-allowed path get
  `file-read-metadata` (SBPL literals) so the kernel can traverse down to it through
  the denied root.
- **Deny writes on the executable routes** — `/usr/bin`, `/bin`, `/usr/sbin`,
  `/sbin`, `/usr/local/bin`, and the sanctioned tool dirs (`/opt/homebrew/bin`, …),
  emitted **last** so the write-deny wins. Read + execute stay (they're covered by
  `(allow default)`); only writes are refused, so the agent can't overwrite the
  binaries its next launch runs. See
  [Non-negotiable boundaries](filesystem-access-model.md#non-negotiable-boundaries).

Everything **else outside the human homes** — the agent binary and its dylibs,
`/usr`, `/System/Library`, tmp, `/dev`, the loopback socket to Jentic One, and any
provider config the agent legitimately owns — is already permitted by
`(allow default)`, so the profile does **not** have to enumerate it (beyond the
write-deny on exec routes). This is precisely the brittleness a `(deny default)`
profile would have incurred, and why the targeted-deny model avoids it.

## What stays the same

- **`localagent.Classify` and the sensitivity rules stay.** The home root and
  sensitive dotfile dirs remain un-grantable even with 700 removed — the classifier
  is a gate on what the CLI *offers*, not DAC enforcement, and this change does not
  relax it. It now returns a two-class verdict (soft ban vs hard-subtree ban), and
  banned paths get **no grant option at all**, but the sensitivity set is unchanged.
- **Traverse-walk + rwx-leaf ACLs stay.** Removing our `chmod 700` does **not** set
  `~` to `0755`; on macOS a login home is `0700` by OS default, so the agent still
  needs the execute-only traverse ACEs to reach a grant. The leaf grant is still
  required for the agent uid to read/write at all.
- **Lifecycle (grant / list / revoke / reset) unchanged** — grants still persist in
  config; the profile is regenerated from config each launch, so revoke/reset just
  work.

## The trust shift (explicit)

Today, world-readable-sibling confidentiality rests on `chmod 700 ~`
(ownership/mode). After this change it rests on **the confinement profile being
applied**. Consequences:

- **Real secrets (0700 dotfiles) are unaffected** — ownership protects them
  regardless; the sandbox only ever subtracts.
- **World-readable siblings are protected by the profile, not the mode.** Since the
  in-`~` confidentiality now depends on the profile, we must never launch without
  it — see error-closed below.

## Error-closed — no unconfined fallback

When confinement is unavailable — `sandbox-exec` absent/removed on macOS, or no
unprivileged user namespaces / no `bwrap` on Linux — `jentic run` **refuses to
launch**. It does *not* fall back to an unconfined session, because that would
silently reinstate the very sibling leak the layer exists to close, now without the
`chmod 700 ~` that used to backstop it.

The failure is not meant to be a big deal for the operator: we surface a clear
message that **fully locked-down agent sessions aren't available on this machine**
and point them at an alternative isolation route (e.g. Dockerising their agent),
rather than dumping a low-level error. The agent account + ACL grants still exist;
what's missing is the process-confinement layer, so the honest thing is to decline
the locked-down launch and say why.

## Open risks (carried from the model doc)

- **macOS `sandbox-exec` is deprecated/undocumented** (still functional; underlies
  the App Sandbox). Risk is a *future* macOS removing the CLI wrapper — at which
  point confinement is unavailable and we **error closed** (above), not silently
  downgrade.
- **Linux unprivileged userns is gated on some hardened distros** → detection, then
  error-closed with the same guidance if unavailable.
- **Profile authoring** — the targeted-home-deny model keeps this tractable (we only
  name `~` and the granted subpaths), but a smoke test should still confirm a
  granted dir is reachable and a world-readable sibling is not.

## Sequencing

1. **This doc** (design first). ✅
2. Confinement shim + profile generation, gated on availability detection; when
   unavailable it errors closed (never launches unconfined). ✅
   (`cli/internal/localagent/confine.go`)
3. Remove the `chmod 700 ~` enforcement. ✅
4. Smoke: confined launch can reach a granted dir and **cannot** read a
   world-readable sibling; the unavailable path refuses to launch with the
   Docker-alternative guidance.
