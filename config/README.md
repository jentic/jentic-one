# Configuration

Application configuration files for jentic-one.

## Files

| File | Purpose |
|------|---------|
| `local.yaml` | Local development (PostgreSQL) — matches `docker/local-setup/docker-compose.yaml` ports |
| `local-sqlite.yaml` | Local development on embedded SQLite (one file per surface under `./.data/`, no external services) — used by `make start-app-sqlite` |
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
