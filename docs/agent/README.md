# Agent runbooks

These files are written **for an AI agent** asked to install and operate a
Jentic One instance on a human's behalf — imperative steps, explicit human
gates, and verification after every stage. Everything runs with `docker`,
`curl`, and the `jentic` CLI; no interactive setup tooling is involved.
(Humans are welcome too; the human-first guides live in
[`docs/installation/`](../installation/quickstart.md).)

Start at the repository root's [`llms.txt`](../../llms.txt), then:

| File | When to read it |
| ---- | --------------- |
| [install.md](install.md) | Standing up an instance: preflight → config → compose → migrations → first admin (human gate) → agent registration (human gate) → verify |
| [operate.md](operate.md) | Day 2: status, start/stop, logs, upgrade, reinstall rules, uninstall |
| [use.md](use.md) | Working against a running instance: the discover → access → execute loop, machine-mode conventions |
| [troubleshoot.md](troubleshoot.md) | When a step fails: symptom-keyed causes and recoveries |
| [harden.md](harden.md) | Before storing a real credential: exposure tiers, TLS, secrets, agent isolation |

Two invariants every file repeats because they are the two ways to break an
install: never regenerate `credentials.encryption` over existing data, and
always register with the exact `auth.canonical_base_url`
(`127.0.0.1`, never `localhost`, on a local install).
