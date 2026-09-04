# Harden a Jentic One install — agent runbook

Read this **before the install touches a real credential**. It distils the
security decisions an install must get right, drawn from the
[security guide](../security/security.md) (the full threat model — link the
human there for the reasoning). Everything here is a concrete change you can
make to the artifacts from [install.md](install.md).

## The one rule that dominates everything

**Keep the agent off the machine that runs Jentic One.** An agent running as
the same OS user can read the SQLite database, the config file, and the
encryption key directly off disk — no API-level control survives that. The
tiers below assume increasing credential value:

| Tier | Posture |
| ---- | ------- |
| Trying it out, throwaway keys | Same machine is acceptable; keep the loopback defaults. |
| Real but low-value credentials | Same machine only with agent isolation (below); prefer separate machines. |
| Production credentials | Separate hosts for instance and agents; TLS everywhere; Postgres; follow [docker.md](../installation/docker.md) / [helm.md](../installation/helm.md). |

## Network exposure

The install runbook publishes every port on `127.0.0.1`. Exposure decisions,
in order of preference:

1. **Loopback (default).** Nothing reachable off-machine. Remote agents
   cannot connect — that is the point.
2. **Reverse proxy with TLS (recommended for any remote access).** Keep the
   compose publishes on `127.0.0.1` and put nginx/Caddy/Traefik in front,
   terminating TLS for two hostnames — the app (`https://jentic.example.com`
   → `127.0.0.1:8000`) and the broker (`https://broker.jentic.example.com` →
   `127.0.0.1:8100`). Then:
   - Set `auth.canonical_base_url` in `jentic-one.yaml` to the public app URL,
     and the `credentials.providers.direct_oauth2.redirect_uri` to
     `<that URL>/credentials/oauth/callback`.
   - Agents register with **exactly** that URL (`jentic register --url
     https://jentic.example.com --broker-url https://broker.jentic.example.com`).
     The token audience is an exact string match: URL drift = `invalid_grant`.
   - The CLI refuses plain `http://` bearers to any non-loopback host by
     design. Do not "fix" that with a proxy that downgrades to http.
3. **Direct LAN bind (discouraged).** Changing the compose publish prefix
   (e.g. `192.168.1.10:8000:8000` or `0.0.0.0:…`) exposes the app, UI, and
   broker with no TLS. Only for trusted, isolated networks.

Never publish the database port. The Postgres variant deliberately has no
`ports:` on the `db` service — the app and broker reach it over the compose
network. Adding a host publish is a debugging convenience and was a real
exposure vector in past audits.

## Secrets and files

- **Agent-run installs:** ask the human up front whether the installing agent
  may see the instance secrets. If not, use the
  [hardened install variant](install.md#hardened-install--the-human-holds-the-secrets)
  — the agent writes placeholders and the human fills the secrets in their own
  shell, so no value enters the agent's context or transcript. Be clear about
  the limit: this is containment you can verify from the transcript, not
  enforcement — an agent running as the OS user that owns `~/.jentic` could
  still read the files afterwards. Enforcement requires a separate OS user or
  machine (the rule above).
- `~/.jentic` must stay `0700` — it is the host-side wall around a
  world-readable config (`0644`, the container's uid 999 must read it) and a
  world-writable logs dir.
- The four generated secrets and the encryption key never appear in argv,
  chat, or logs. Passwords (`create-admin`, `reset-password`) are piped on
  stdin, never flags.
- **Encryption key lifecycle:** the `credentials.encryption` block is the root
  of credential custody. Reuse it verbatim on every reinstall; rotate it only
  via the multi-key procedure (add `v2` entry, flip `active_id`, keep `v1`).
  A lost or clobbered key = every stored credential unrecoverable
  (`credential_undecryptable` on execute).
- Config drift: any key can also be supplied as a `JENTIC__*` env var
  ([reference](../reference/config.md)) — useful to keep secrets out of the
  file entirely (e.g. injected from a secret manager in the compose file).

## Agent-side hardening

- **Least privilege by construction:** approve agents with narrow permission
  rules (`allow` rules constrained by method/path), and review the `--reason`
  on every access request. The rules match method/path/operation — never the
  request body; do not accept a rule that pretends otherwise.
- **One identity per agent.** Each machine/agent registers its own identity so
  it can be individually approved, scoped, audited, and revoked (revoke in
  `/app/agents`; the upstream API key is untouched).
- **Same-host isolation (when separate machines are not possible):**
  `jentic setup` can create a dedicated Unix user for the coding agent and
  `jentic run <agent>` launches it confined (macOS `sandbox-exec` / Linux
  `bwrap`), with the credential store outside the agent's reach. See the
  [local agent guide](../guides/local-agent.md) and
  [sandbox design](../security/same-host/README.md).
- The agent holds a token, never an upstream API credential. If an agent is
  compromised, it can only make the calls it was already allowed to make —
  and every one is in the audit log.

## Operational hygiene

- Pin the image by digest (`ghcr.io/jentic/jentic-one-app@sha256:…`) and
  verify cosign signatures before the image crosses into a locked-down
  network: [installation/quickstart.md](../installation/quickstart.md).
- Keep `runtime.debug: false` and `log_level: INFO` in production; the JSON
  log sink (`~/.jentic/logs/app.jsonl`) redacts secrets by design, but debug
  logging is noisier and slower.
- Back up the data volume before every upgrade ([operate.md](operate.md)).
- Telemetry is consent-based and recorded explicitly in the config either way;
  an opted-out config carries no instance identifier.
