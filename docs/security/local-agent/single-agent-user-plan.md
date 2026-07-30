# One agent Unix user for all profiles

> **Status:** plan (approved, in progress). Supersedes the per-agent-type
> account model. Absorbs and completes
> [`profile-run-as-agent-plan.md`](profile-run-as-agent-plan.md) (run-as is the
> final phase here).

## Goal

A human sets up the dedicated agent Unix account **once**. From then on every
`jentic run` — whatever coding agent, whatever profile — goes through that single
account. Registering a new agent identity adds a *profile* inside the shared
account's home, not a new OS user. A human who never enables the agent account
uses the CLI exactly as before; nothing about the isolated path leaks into the
ordinary flow.

## Decisions (fixed)

1. **Scope: one agent user per human.** The account name stays
   `<operator>-local-agent`. Grants a human makes (ACLs under their own home) are
   never reachable by another human, because each human has their own agent uid.

2. **On/off is config state the CLI always reads — not an exposed toggle.** The
   operator config records whether the agent account exists (`account_created`)
   and an `enabled` flag reserved for a future soft-pause. There is **no**
   `enable`/`disable` command right now: behaviour keys off whether the account
   exists. A `jentic reset` that deletes the Unix account flips `enabled` to
   false. A human with no agent account is fully empowered to use the CLI as
   normal — every agent-account code path is bypassed.

3. **Grants consolidated onto the account, recorded operator-side.** One
   `granted_dirs` list on the account (the ACLs are physically one set — same
   uid — regardless of which agent binary launched). It lives in the human's
   `~/.jentic/config.yaml` so the agent cannot edit its own access list.

4. **Profiles: human-owned vs agent-account-owned.** The human's own profiles
   stay in `~/.jentic/profiles`. Once the account exists, **newly
   registered/bootstrapped agent identities are written into the shared agent
   home** (`<config_dir>/profiles`), chowned to the agent. The operator side
   keeps only the account-level `config_dir` pointer; `discoverProfiles` scans it
   (no per-profile bookkeeping). If humans later gain their own multi-profile
   support, that data lives in the human's home.

5. **`<agent>` selects the binary; the checked-out profile selects identity.**
   - `jentic run <agent>` — `<agent>` (claude, …) picks the *binary/descriptor*
     only.
   - **Registering/bootstrapping a profile checks it out** (makes it the agent
     account's currently-selected profile). It does **not** change the human
     operator's own `default_profile`.
   - `jentic run` **injects `JENTIC_PROFILE` = the checked-out agent profile**
     into the confined session, so the launched agent uses whatever profile is
     checked out without the human passing a flag. Storage: the checked-out
     profile is the agent home's own `default_profile`
     (`<config_dir>/config.yaml`), which register/bootstrap already writes when
     it targets the agent home; the operator's `default_profile` is a separate
     file and is left untouched.
   - `jentic run --profile <name>` overrides for one invocation (validated
     against the agent home), injecting `JENTIC_PROFILE=<name>`.

## Phases

**Phase 1 — Config schema + migration.** Reshape `config.LocalAgents` (a
per-agent-type map) into one operator-side `AgentAccount`: `user`, `home_dir`,
`config_dir`, `account_created`, `enabled`, consolidated `granted_dirs`, and the
set of installed agent binaries. Transparent migration — the existing
`<operator>-local-agent` already *is* the shared user, so this is a reshape plus
a grant-merge across the old per-type entries. New `config.AgentAccount`
accessors.

**Phase 2 — `run` account-aware.** `<agent>` resolves the binary/descriptor;
account resolution is account-scoped. No account / `account_created=false` →
run same-user exactly as a non-isolated user does today. Inject `JENTIC_PROFILE`
from the checked-out profile (or `--profile`). `--list-grants`/`--revoke`/
`--grant` operate on the account.

**Phase 3 — Registration lands in the shared home.** `register` + `bootstrap`
become account-aware beyond "created in this run": when the account exists, a
new identity is written into `<config_dir>/profiles`, chowned to the agent, and
**checked out** (agent-home default). The operator side keeps only the
account-level pointer.

**Phase 4 — reset semantics (functionality unchanged, granularity refined).**
- `jentic reset` — every profile + the account (Unix user, home disposition,
  sudoers, grants). Flips `enabled` off. (As today, whole-slate.)
- `jentic reset <profile>` — remove just that profile. If it is the **last**
  agent profile, offer to also tear down the account (grants, sudoers, Unix
  user, home) — i.e. optionally expose what full reset does. Grants/sudoers/
  account come off only at account teardown, never on a single-profile removal.
- The argument is treated as a profile name; a helpful error if it collides
  with a known binary id.

**Phase 5 — run-as (final; makes agent-owned profiles usable).** When the active
profile is agent-owned, profile-scoped commands (`execute`, catalog) resolve
**that profile's** tokens from the agent store (the operator already has ACL read
access) and call the control-plane **in-process as the operator** — loopback +
the agent's bearer token, no filesystem writes, so no re-exec or confinement is
needed. `profile use` stops refusing agent-owned profiles; `profile list` marks
an active agent-owned profile the same way it marks an operator one. This
completes and replaces [`profile-run-as-agent-plan.md`](profile-run-as-agent-plan.md).

## Cross-cutting

- `discoverProfiles` / `profile view` / seeding guards become account-scoped
  (one account, not per-agent-type).
- Regenerate `ui/public/cli-reference.json` whenever help text changes.
- Update [`local-agent-isolation.md`](local-agent-isolation.md) and
  [`filesystem-access-model.md`](filesystem-access-model.md); mark the run-as
  plan implemented on completion.

## Non-negotiable boundaries (unchanged)

Hard-banned dotfile dirs stay hard-banned; provider credentials cross into the
agent account only explicitly and with a warning; confinement stays error-closed;
the agent never gains write access to the operator's grant records.
