# Browser-session hijack (issue #3)

> The class of attack that survives even a *perfect* Axis-A separation: the agent
> never touches the key or DB, but it drives the **human operator's authenticated
> session** and acts as the operator. A confused-deputy / session-riding attack.

## Why this breaks the paradigm even with DB isolation

Suppose we do everything right on Axis A — Jentic One runs as a different user or
on a separate host, the agent cannot read the keyset or the DB. The operator still
has to *administer* it: they log into the UI (or the admin API) from a browser,
typically **on the same machine as the agent**. That authenticated session is now
an asset in the agent's blast radius, and with it the agent can do everything a
hijacked `org:admin` can (see [`01`](01-threat-model.md) AP-2 / [`04`](04-current-state.md)):
provision a fully-scoped agent, mint API keys, approve its own access requests,
create credentials. It gets **credential *use* and provisioning** without ever
seeing a secret and without touching the key file.

## How an agent gets at the session (concrete vectors)

Current mechanics (`04-current-state.md`): the admin token is a stateless HS256
JWT stored in **`localStorage` (`jentic-one.access_token`)**, sent as an
`Authorization: Bearer` header, no cookie, 1-hour TTL.

- **V1 — read `localStorage` from the browser profile on disk.** A same-user
  agent can read the browser's LevelDB/SQLite profile files and lift the JWT
  directly. No browser automation needed. *(Bearer-in-`localStorage` is easier to
  lift at rest than an `HttpOnly` cookie.)*
- **V2 — drive a headless/automated browser with the operator's profile.** If the
  agent can launch Chrome with `--user-data-dir` pointing at the real profile (or
  attach over the DevTools/CDP port), it inherits the logged-in session and can
  click through the UI as the operator.
- **V3 — attach to an existing browser via CDP / an extension.** If the operator
  runs Chrome with `--remote-debugging-port` (or the agent can install/drive an
  extension), the agent scripts the live tab.
- **V4 — the agent *is* given browser control as a feature** (computer-use /
  browser-tool agents). Here browser-driving is the intended capability, so the
  session is reachable by design whenever the operator is logged in.

Note **V1 does not even need the browser to be running** — the JWT sits in a file
until it expires. The 1-hour TTL is the main limiter today.

## Why this is genuinely hard

- The session is a **legitimate, human-authenticated** artefact. The server cannot
  distinguish "operator clicked" from "agent drove the operator's tab" — same
  token, same origin, same headers. CSRF defenses don't apply (it's not a
  cross-site ambient-cookie ride; it's same-origin use of a real token).
- It rides the **operator's** identity, so scope caps on *agents* don't touch it.
- We **don't own the browser or the agent**, so we can't mandate profile
  isolation.

## What actually reduces this risk (preview; detail in [`03`](03-mitigations.md))

The theme: **make the operator credential expensive to steal, short-lived, and
low-privilege**, and **move destructive actions behind a channel the agent can't
ride.**

1. **Don't leave a bearer token at rest in `localStorage`.** An `HttpOnly`,
   `SameSite=Strict`, `Secure` cookie session is not readable by V1 (can't be read
   from JS or lifted as easily from the profile) — at the cost of needing CSRF
   protection and a session store. Trade-off, not a pure win; see `03`.
2. **Short, sliding sessions + explicit re-auth for dangerous actions.** Minting
   keys, replacing scopes, approving access, creating credentials → require a
   fresh **step-up** (re-enter password / WebAuthn touch) that a driven browser
   can't satisfy silently.
3. **Phishing-resistant, presence-requiring step-up (WebAuthn / passkey).** A
   hardware/passkey touch on sensitive mutations defeats V1–V4 because the agent
   can't produce the gesture. This is the strongest single lever against issue #3.
4. **Separate the *operator console* from the *agent's host* (Axis B).** If the
   operator administers Jentic One from a different machine than the agent runs on,
   V1–V3 evaporate; only V4 (an agent explicitly told to browse to the console
   with valid creds) remains, and step-up catches that.
5. **Bind the operator session to context** — device-bound tokens (DPoP /
   token-binding) so a lifted token can't be replayed from another process/host;
   sensitive-action-specific short-lived tokens.
6. **Make dangerous mutations *reviewable and reversible*** — e.g. new access
   grants take effect after a delay / require a second approver / emit a
   high-signal out-of-band alert, so a silent self-grant is caught.

## Bottom line

Issue #3 is the reason "put Jentic One on a VPC" is necessary **but not
sufficient**. The VPC closes AP-1/AP-2; it does not close the operator-session
path unless the operator *also* administers from outside the agent's host and
sensitive actions require a presence gesture the agent can't forge. Treat
**agent-with-browser-control as its own tier** that demands operator-console
separation + WebAuthn step-up, not just network separation.
