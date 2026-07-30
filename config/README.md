# Configuration

Application configuration files for jentic-one.

## Files

| File | Purpose |
|------|---------|
| `local.yaml` | Local development — matches `docker/local-setup/docker-compose.yaml` ports |
| `production.yaml.example` | Production template — copy and fill in real values |

## Usage

Point the app at a config file via the `JENTIC_CONFIG_FILE` environment variable:

```bash
JENTIC_CONFIG_FILE=config/local.yaml make start-app
```

Or place a `jentic-one.yaml` in the project root (auto-detected).

## Environment variable overrides

Any config value can be overridden with an environment variable using the
`JENTIC__SECTION__KEY` convention (double underscores, all uppercase):

```bash
JENTIC__DATABASES__REGISTRY__PASSWORD=secret
JENTIC__RUNTIME__LOG_LEVEL=DEBUG
JENTIC__APPS=registry,control
```

Secrets should always be supplied via environment variables rather than
committed to config files.

## Web session lifetimes (`admin.auth`)

| Key | Default | Meaning |
|-----|---------|---------|
| `admin.auth.jwt_ttl_seconds` | `3600` (1 h) | Lifetime of a single web-session JWT issued by `POST /auth/login`. An *open* tab renews the token via `POST /auth/refresh` before it expires (the renewal is timer-driven, not tied to user activity), so this bounds how long a *closed* tab's token stays usable — not how long an idle-but-open session survives. |
| `admin.auth.session_ttl_seconds` | `43200` (12 h) | Absolute cap on a web session. Refresh is refused (401 `session_expired`) once the original password login is older than this window, so even a continuously-active session must re-authenticate — and a leaked token cannot be kept alive indefinitely. A refresh granted just inside the window still mints a full-TTL token, so the last token can outlive the window by up to `jwt_ttl_seconds`; size the window with that margin in mind. Must be ≥ `jwt_ttl_seconds` to be meaningful. |
