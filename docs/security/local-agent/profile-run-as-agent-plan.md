# Switching to an agent-owned profile should run operations as the agent

> **Status:** deferred plan (not implemented). **Merge prerequisite:** this must
> land before the local-agent-isolation branch merges — today the operator can
> *see* an agent-owned profile but not switch to one.

## Where we are today

`jentic profile` (interactive picker) and `jentic profile list` work off the
**same** discovered profile set: the operator's own profiles under
`~/.jentic/profiles`, plus any profile a local agent registered as its own Unix
user wrote into its home (`<ConfigDir>/profiles`). Discovery is
`App.discoverProfiles`; an agent-owned profile carries a non-empty `agentID`
(`profileRef.owned()`), and the operator can read it because account creation
grants a recursive, inherited ACL over the agent home
(`localagent.GrantOperatorHomeCmd`).

Both surfaces **list** agent-owned profiles. Selecting one is deliberately
refused for now: `profileSwitch` returns a pointer to this document rather than
setting it as the operator's active default. That is because setting it as the
active profile *looks* like it would work but wouldn't do the thing the operator
expects — see below.

## The intended behaviour

When the operator switches to an **agent-owned** profile, every subsequent
operation invoked under that profile should execute **as the agent's Unix
account**, not as the operator. The active profile then means "act as this
agent": the agent's identity, the agent's credential store, the agent's granted
directories — and, critically, the agent's confinement.

This is a *run-as* mechanism, not just a config pointer. Merely recording the
agent profile as `default_profile` in the operator's `config.yaml` would leave
operations running as the operator against files the operator can't legitimately
act on as the agent (and would bypass the very isolation the dedicated account
exists to provide). So the switch has to carry an execution-context change.

## What has to be built

1. **Active-profile resolution learns ownership.** `config.ResolvedProfileName`
   / profile loading must resolve an active *agent-owned* profile to its
   `profileRef` (store path + `agentID`), not assume `a.Paths`. The profile store
   is already parameterised by `config.Paths`, so the read path is close; the
   write/active-state path is what needs the ownership dimension.

2. **A run-as launch path.** Operations executed under an agent-owned active
   profile must re-enter as the agent user, under the same per-session
   confinement `jentic run` uses (`sandbox-exec` / `bwrap`, error-closed —
   see [`sandbox-exec-plan.md`](sandbox-exec-plan.md)). This likely means
   routing catalog/execute operations through the same privileged-drop +
   confinement wrapper rather than running them in-process as the operator.

3. **Credential-boundary review.** Running as the agent means reading the
   agent's tokens and provider credentials from the agent home. The hard-banned
   dotfile dirs and the "provider credentials cross the boundary only explicitly
   and with a warning" rule still hold; the run-as path must not become a way to
   read operator secrets by proxy.

4. **UX.** The picker already tags agent-owned rows `(agent: <id>)`. Once
   run-as works, selecting one should switch and make clear that subsequent
   commands run as that agent; `profile list` should mark an active agent-owned
   profile the same way it marks an active operator profile (today only an
   operator-owned profile is treated as activatable in the list glyph logic).

## Interim contract (what ships now)

- `profile` and `profile list` show the identical discovered set.
- The picker lists agent-owned profiles and lets the cursor land on them.
- Confirming an agent-owned profile fails fast with a message pointing here,
  rather than silently writing a default that would not run-as.
- Operator-owned profiles switch exactly as before.

See also [`filesystem-access-model.md`](filesystem-access-model.md) for the
profile-discovery mechanics and [`local-agent-isolation.md`](local-agent-isolation.md)
for the account model.
