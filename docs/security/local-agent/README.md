# Running local coding agents on the credential boundary

> **Status:** analysis / design exploration. Not yet a committed plan.

Jentic One keeps third-party credentials off the *network* path (the Broker
injects them; the agent only sees responses). But the one-line install lands most
users with the agent and Jentic One running as the **same OS user on the same
machine**, where that guarantee doesn't hold — a compromised or prompt-injected
agent can read the keys straight off disk. These docs work through isolating the
agent as its own unprivileged Unix user so the isolated posture is the default.

| Doc | What it covers |
| --- | -------------- |
| [`analysis.md`](analysis.md) | **The problem.** The same-user attack paths (AP-1…AP-4), why isolating the *agent* is strictly stronger than isolating Jentic One, and the state before this work. |
| [`local-agent-isolation.md`](local-agent-isolation.md) | **The design.** Run the agent as a dedicated Unix user, wrapped by the `jentic` CLI: account creation folded into `bootstrap` / `wizard`, then `jentic run <agent>`, directory grants, and `jentic reset`. |
| [`filesystem-access-model.md`](filesystem-access-model.md) | **The permission mechanics.** How the agent's and operator's accounts reach — and are kept out of — each other's files: traverse-walk + rwx-leaf ACLs, the inherited operator grant, per-session process confinement, and the grant/revoke/reset lifecycle. |
| [`sandbox-exec-plan.md`](sandbox-exec-plan.md) | **The confinement layer.** The per-session process confinement (`sandbox-exec` on macOS, `bwrap` on Linux) that closes the sibling-traversal leak and replaces the `chmod 700 ~` default-deny: why it's a targeted human-home deny, why it errors closed, and the non-negotiable boundaries (whole-`/Users`+`/home` deny, read-only exec routes). |
| [`agent-home-under-operator-home-plan.md`](agent-home-under-operator-home-plan.md) | **A deferred plan (not implemented).** What it would take to default the agent home to `~/jentic-agents/<agent>` instead of the shared parent — the one new security tradeoff (a persistent execute ACE on `~`), and the open questions to resolve first. |
| [`profile-run-as-agent-plan.md`](profile-run-as-agent-plan.md) | **A deferred plan (merge prerequisite).** Switching the active profile to an agent-owned one should run subsequent operations *as* the agent's Unix user. Today both `profile` and `profile list` show agent-owned profiles, but switching to one is refused pending this run-as mechanism. |

For the broader deployment guidance (separate host / network postures) see
[`../hardening.md`](../hardening.md).
