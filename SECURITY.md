# Security Policy

## Supported Versions

Security updates are applied to the latest release on the `main` branch. Older
versions are not maintained separately.

## Reporting a Vulnerability

If you discover a security vulnerability in Jentic One, please report it
responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, email **compliance@jentic.com** with:

- A description of the vulnerability
- Steps to reproduce or a proof of concept
- The potential impact and severity (your assessment)
- Any suggested fix (optional)

## Disclosure Policy

We follow coordinated disclosure. We ask that you:

- Allow us reasonable time to investigate and address the vulnerability before
  public disclosure
- Avoid accessing or modifying data that isn't yours
- Act in good faith

We will credit reporters in release notes unless anonymity is requested.

## Security Model — what operators should know

Jentic One keeps credential custody within the operator's deployment:

- Stored credentials are **encrypted at rest**. The credential creation response
  returns cleartext secret material once; subsequent reads and rotation responses
  are redacted.
- The Broker decrypts credentials for governed execution. Control also handles
  cleartext secret material during creation and rotation and decrypts managed
  OAuth secrets during connect and refresh flows.
- The intended upstream receives the injected credential. Broker responses are
  passthrough responses, so a reflective or malicious upstream can return
  request data, including an injected credential, to the caller. Register only
  trusted upstreams for credentialed execution.
- Jentic One does not intentionally write secret values to application logs.
- The credential-at-rest encryption keyset is **required** and must be supplied
  by the operator (environment variable or secret manager). Never commit a real
  key to source control.
- Access is governed by fine-grained, per-binding permissions, and every
  execution is written to an append-only audit log.
- Jentic One does **not** send telemetry by default. Anonymous product telemetry
  is **opt-in** (`telemetry.enabled: true`); when enabled it sends a small, fixed,
  closed-schema event set — `{id, version, event, actor_type?, tags?, ts}`, where
  `event`/`actor_type` are fixed enums and `tags` are fixed labels (e.g. the OS
  family `linux`/`darwin`/`windows`/`other`, sent once per boot on the
  `instance_booted` event) — with no credentials, request data, or PII.
  Observability exporters are self-hosted.

> **Most important operator guidance:** API-level controls do not protect
> credentials from an agent running as the same OS user as Jentic One. A
> same-user process can read the encryption key and database directly. Do not
> run the broker in the same trust boundary as your agent for real credentials.
> See **[docs/security/hardening.md](docs/security/hardening.md)** for the
> deployment-tier ladder, agent-sandboxing options, and a production checklist.

## Secrets in the Repository

This repository runs `detect-secrets` as a pre-commit hook. Never commit
credentials, API keys, tokens, or private keys. Configuration secrets are
supplied at runtime via environment variables or a secret manager.

## Code of Conduct

Participation in this project is governed by the Jentic
[Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md).
