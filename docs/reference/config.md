# Configuration reference

<!-- GENERATED FILE — do not hand-edit. Regenerate with `make config-reference`
     (tools/config_reference.py). Drift-gated by
     tests/arch/test_config_reference_conformance.py. -->

Every configuration key jentic-one reads, generated from the
[`AppConfig`](../../src/jentic_one/shared/config.py) Pydantic model — the same
source of truth behind [`config/config-schema.json`](../../config/config-schema.json)
and the `jenticctl install` wizard. How the config system works (loading,
`Context`, hot reload) is covered in
[Context and configuration](../development/context-and-config.md).

## How a value is resolved

Highest wins:

1. Environment variable (`JENTIC__SECTION__KEY`)
2. YAML config file (`jentic-one.yaml` in the working directory, or the file
   named by `JENTIC_CONFIG_FILE` — a loader-level variable, not a config key)
3. The field default listed below

Env-var naming: uppercase the YAML path and join nesting levels with `__`
(single underscores within a key are preserved). A `<name>` segment is a
map key you choose; a `<n>` segment is a zero-based list index
(`JENTIC__AUTH__ID_SIGNING__0__KID`). List- and model-valued fields are
usually easier to express in YAML.

A default of *required* means the app refuses to start without a value; `—`
means the default is empty or built at runtime (an empty list/map).
The open `extensions` section (enterprise overlay sub-configs registered at
import time) is machine state, not an operator knob, and is excluded here just
as it is from the JSON schema.

## `databases`

Named database connections.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `databases.registry.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__REGISTRY__BACKEND` |  |
| `databases.registry.host` | string | `"localhost"` | `JENTIC__DATABASES__REGISTRY__HOST` |  |
| `databases.registry.port` | integer | `5432` | `JENTIC__DATABASES__REGISTRY__PORT` |  |
| `databases.registry.name` | string | `""` | `JENTIC__DATABASES__REGISTRY__NAME` |  |
| `databases.registry.user` | string | `"postgres"` | `JENTIC__DATABASES__REGISTRY__USER` |  |
| `databases.registry.password` | string (secret) | `""` | `JENTIC__DATABASES__REGISTRY__PASSWORD` |  |
| `databases.registry.pool_max` | integer | `10` | `JENTIC__DATABASES__REGISTRY__POOL_MAX` |  |
| `databases.registry.schema_name` | string | `"public"` | `JENTIC__DATABASES__REGISTRY__SCHEMA_NAME` |  |
| `databases.registry.path` | string \| null | `null` | `JENTIC__DATABASES__REGISTRY__PATH` |  |
| `databases.registry.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__REGISTRY__BUSY_TIMEOUT_MS` |  |
| `databases.registry.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__REGISTRY__JOURNAL_MODE` |  |
| `databases.admin.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__ADMIN__BACKEND` |  |
| `databases.admin.host` | string | `"localhost"` | `JENTIC__DATABASES__ADMIN__HOST` |  |
| `databases.admin.port` | integer | `5432` | `JENTIC__DATABASES__ADMIN__PORT` |  |
| `databases.admin.name` | string | `""` | `JENTIC__DATABASES__ADMIN__NAME` |  |
| `databases.admin.user` | string | `"postgres"` | `JENTIC__DATABASES__ADMIN__USER` |  |
| `databases.admin.password` | string (secret) | `""` | `JENTIC__DATABASES__ADMIN__PASSWORD` |  |
| `databases.admin.pool_max` | integer | `10` | `JENTIC__DATABASES__ADMIN__POOL_MAX` |  |
| `databases.admin.schema_name` | string | `"public"` | `JENTIC__DATABASES__ADMIN__SCHEMA_NAME` |  |
| `databases.admin.path` | string \| null | `null` | `JENTIC__DATABASES__ADMIN__PATH` |  |
| `databases.admin.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__ADMIN__BUSY_TIMEOUT_MS` |  |
| `databases.admin.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__ADMIN__JOURNAL_MODE` |  |
| `databases.control.backend` | "postgres" \| "sqlite" | `"postgres"` | `JENTIC__DATABASES__CONTROL__BACKEND` |  |
| `databases.control.host` | string | `"localhost"` | `JENTIC__DATABASES__CONTROL__HOST` |  |
| `databases.control.port` | integer | `5432` | `JENTIC__DATABASES__CONTROL__PORT` |  |
| `databases.control.name` | string | `""` | `JENTIC__DATABASES__CONTROL__NAME` |  |
| `databases.control.user` | string | `"postgres"` | `JENTIC__DATABASES__CONTROL__USER` |  |
| `databases.control.password` | string (secret) | `""` | `JENTIC__DATABASES__CONTROL__PASSWORD` |  |
| `databases.control.pool_max` | integer | `10` | `JENTIC__DATABASES__CONTROL__POOL_MAX` |  |
| `databases.control.schema_name` | string | `"public"` | `JENTIC__DATABASES__CONTROL__SCHEMA_NAME` |  |
| `databases.control.path` | string \| null | `null` | `JENTIC__DATABASES__CONTROL__PATH` |  |
| `databases.control.busy_timeout_ms` | integer | `5000` | `JENTIC__DATABASES__CONTROL__BUSY_TIMEOUT_MS` |  |
| `databases.control.journal_mode` | string | `"WAL"` | `JENTIC__DATABASES__CONTROL__JOURNAL_MODE` |  |

## `services`

Service-level settings (immutable after boot).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `services.request_timeout_s` | number | `30.0` | `JENTIC__SERVICES__REQUEST_TIMEOUT_S` |  |
| `services.retry_max` | integer | `3` | `JENTIC__SERVICES__RETRY_MAX` |  |
| `services.retry_backoff_s` | number | `1.0` | `JENTIC__SERVICES__RETRY_BACKOFF_S` |  |

## `worker`

Background job-worker durability knobs (§09 E4.2). The worker claims a job, sets a **visibility deadline** (`visibility_timeout_s` from claim), and processes it. A job left `RUNNING` past that deadline by a crashed worker/pod is reclaimed on a later poll. A handler failure requeues the job with capped exponential backoff up to `max_attempts` claims; beyond that it is dead-lettered (poison-message handling) rather than looped forever.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `worker.visibility_timeout_s` | number | `120.0` | `JENTIC__WORKER__VISIBILITY_TIMEOUT_S` |  |
| `worker.max_attempts` | integer | `5` | `JENTIC__WORKER__MAX_ATTEMPTS` |  |
| `worker.retry_backoff_base_s` | number | `2.0` | `JENTIC__WORKER__RETRY_BACKOFF_BASE_S` |  |
| `worker.retry_backoff_max_s` | number | `60.0` | `JENTIC__WORKER__RETRY_BACKOFF_MAX_S` |  |
| `worker.drain_timeout_s` | number | `25.0` | `JENTIC__WORKER__DRAIN_TIMEOUT_S` |  |

## `runtime`

Hot-reloadable runtime flags.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `runtime.debug` | boolean | `false` | `JENTIC__RUNTIME__DEBUG` |  |
| `runtime.log_level` | string | `"INFO"` | `JENTIC__RUNTIME__LOG_LEVEL` |  |
| `runtime.maintenance_mode` | boolean | `false` | `JENTIC__RUNTIME__MAINTENANCE_MODE` |  |

## `logging`

File logging sink (in addition to stdout).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `logging.file_enabled` | boolean | `false` | `JENTIC__LOGGING__FILE_ENABLED` |  |
| `logging.file_dir` | string | `".jentic/logs"` | `JENTIC__LOGGING__FILE_DIR` |  |
| `logging.file_name` | string | `"app.log"` | `JENTIC__LOGGING__FILE_NAME` |  |
| `logging.file_max_bytes` | integer | `10485760` | `JENTIC__LOGGING__FILE_MAX_BYTES` |  |
| `logging.file_backup_count` | integer | `5` | `JENTIC__LOGGING__FILE_BACKUP_COUNT` |  |

## `server`

HTTP server settings.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `server.host` | string | `"0.0.0.0"` | `JENTIC__SERVER__HOST` |  |
| `server.port` | integer | `8000` | `JENTIC__SERVER__PORT` |  |
| `server.reload` | boolean | `false` | `JENTIC__SERVER__RELOAD` |  |
| `server.backend` | "local" \| "remote" | `"local"` | `JENTIC__SERVER__BACKEND` |  |

## `observability`

Observability settings (metrics, tracing knobs).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `observability.metrics.exporter` | "otlp" \| "prometheus" \| "none" | `"otlp"` | `JENTIC__OBSERVABILITY__METRICS__EXPORTER` |  |
| `observability.metrics.export_interval_seconds` | integer | `15` | `JENTIC__OBSERVABILITY__METRICS__EXPORT_INTERVAL_SECONDS` |  |
| `observability.tracing.exporter` | "otlp" \| "none" | `"otlp"` | `JENTIC__OBSERVABILITY__TRACING__EXPORTER` |  |

## `admin`

Admin surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `admin.auth.jwt_secret` | string (secret) | `"**********"` | `JENTIC__ADMIN__AUTH__JWT_SECRET` |  |
| `admin.auth.jwt_ttl_seconds` | integer (> 0) | `3600` | `JENTIC__ADMIN__AUTH__JWT_TTL_SECONDS` |  |
| `admin.auth.session_ttl_seconds` | integer (> 0) | `43200` | `JENTIC__ADMIN__AUTH__SESSION_TTL_SECONDS` |  |
| `admin.auth.failed_login_lockout_threshold` | integer | `5` | `JENTIC__ADMIN__AUTH__FAILED_LOGIN_LOCKOUT_THRESHOLD` |  |
| `admin.auth.failed_login_lockout_seconds` | integer | `900` | `JENTIC__ADMIN__AUTH__FAILED_LOGIN_LOCKOUT_SECONDS` |  |
| `admin.invite.ttl_days` | integer | `7` | `JENTIC__ADMIN__INVITE__TTL_DAYS` |  |
| `admin.invite.pepper` | string (secret) | `"**********"` | `JENTIC__ADMIN__INVITE__PEPPER` |  |

## `auth`

Platform-actors OAuth surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `auth.canonical_base_url` | string | `""` | `JENTIC__AUTH__CANONICAL_BASE_URL` |  |
| `auth.access_ttl_seconds` | integer | `3600` | `JENTIC__AUTH__ACCESS_TTL_SECONDS` |  |
| `auth.refresh_ttl_seconds` | integer | `604800` | `JENTIC__AUTH__REFRESH_TTL_SECONDS` |  |
| `auth.rat_ttl_seconds` | integer | `900` | `JENTIC__AUTH__RAT_TTL_SECONDS` |  |
| `auth.claim_ttl_seconds` | integer | `900` | `JENTIC__AUTH__CLAIM_TTL_SECONDS` |  |
| `auth.assertion_max_ttl_seconds` | integer | `300` | `JENTIC__AUTH__ASSERTION_MAX_TTL_SECONDS` |  |
| `auth.auth_code_ttl_seconds` | integer | `300` | `JENTIC__AUTH__AUTH_CODE_TTL_SECONDS` |  |
| `auth.id_signing` | list of SigningKeyConfig | — | `JENTIC__AUTH__ID_SIGNING` |  |
| `auth.id_signing.<n>.kid` | string | *required* | `JENTIC__AUTH__ID_SIGNING__<N>__KID` |  |
| `auth.id_signing.<n>.private_key_pem` | string (secret) | *required* | `JENTIC__AUTH__ID_SIGNING__<N>__PRIVATE_KEY_PEM` |  |
| `auth.idp.enabled` | boolean | `false` | `JENTIC__AUTH__IDP__ENABLED` |  |
| `auth.idp.provider` | string | `"oidc"` | `JENTIC__AUTH__IDP__PROVIDER` |  |
| `auth.idp.issuer` | string | `""` | `JENTIC__AUTH__IDP__ISSUER` |  |
| `auth.idp.client_id` | string | `""` | `JENTIC__AUTH__IDP__CLIENT_ID` |  |
| `auth.idp.client_secret` | string (secret) | `""` | `JENTIC__AUTH__IDP__CLIENT_SECRET` |  |
| `auth.idp.scopes` | list of string | — | `JENTIC__AUTH__IDP__SCOPES` |  |
| `auth.idp.authorization_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__AUTHORIZATION_ENDPOINT` |  |
| `auth.idp.exchange_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__EXCHANGE_ENDPOINT` |  |
| `auth.idp.userinfo_endpoint` | string \| null | `null` | `JENTIC__AUTH__IDP__USERINFO_ENDPOINT` |  |
| `auth.idp.hosted_domain` | string \| null | `null` | `JENTIC__AUTH__IDP__HOSTED_DOMAIN` |  |

## `broker`

Broker surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `broker.upstream_timeout_s` | number | `30.0` | `JENTIC__BROKER__UPSTREAM_TIMEOUT_S` |  |
| `broker.resolve_cache_ttl_seconds` | number | `3.0` | `JENTIC__BROKER__RESOLVE_CACHE_TTL_SECONDS` |  |
| `broker.toolkit_cache_ttl_s` | number | `3.0` | `JENTIC__BROKER__TOOLKIT_CACHE_TTL_S` |  |
| `broker.rule_cache_ttl_s` | number | `3.0` | `JENTIC__BROKER__RULE_CACHE_TTL_S` |  |
| `broker.jobs_api_base_url` | string \| null | `null` | `JENTIC__BROKER__JOBS_API_BASE_URL` |  |
| `broker.jwt_secret` | string (secret) \| null | `null` | `JENTIC__BROKER__JWT_SECRET` |  |
| `broker.jwt_verification.audience` | string \| null | `null` | `JENTIC__BROKER__JWT_VERIFICATION__AUDIENCE` |  |
| `broker.jwt_verification.leeway_s` | number | `60.0` | `JENTIC__BROKER__JWT_VERIFICATION__LEEWAY_S` |  |
| `broker.jwt_verification.trusted_issuers` | list of TrustedIssuerConfig | — | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS` |  |
| `broker.jwt_verification.trusted_issuers.<n>.issuer` | string | *required* | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__ISSUER` |  |
| `broker.jwt_verification.trusted_issuers.<n>.jwks_url` | string | *required* | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__JWKS_URL` |  |
| `broker.jwt_verification.trusted_issuers.<n>.algorithms` | list of string | — | `JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__<N>__ALGORITHMS` |  |
| `broker.account_linking_base_url` | string \| null | `null` | `JENTIC__BROKER__ACCOUNT_LINKING_BASE_URL` |  |
| `broker.resilience.max_in_flight` | integer | `200` | `JENTIC__BROKER__RESILIENCE__MAX_IN_FLIGHT` |  |
| `broker.resilience.shed_retry_after_s` | integer | `5` | `JENTIC__BROKER__RESILIENCE__SHED_RETRY_AFTER_S` |  |
| `broker.resilience.request_deadline_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__REQUEST_DEADLINE_S` |  |
| `broker.resilience.readiness_saturation_threshold` | number (> 0.0, <= 1.0) | `0.9` | `JENTIC__BROKER__RESILIENCE__READINESS_SATURATION_THRESHOLD` |  |
| `broker.resilience.upstream.connect_timeout_s` | number | `5.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__CONNECT_TIMEOUT_S` |  |
| `broker.resilience.upstream.read_timeout_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__READ_TIMEOUT_S` |  |
| `broker.resilience.upstream.write_timeout_s` | number | `30.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__WRITE_TIMEOUT_S` |  |
| `broker.resilience.upstream.pool_timeout_s` | number | `2.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__POOL_TIMEOUT_S` |  |
| `broker.resilience.upstream.http2` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__HTTP2` |  |
| `broker.resilience.upstream.max_connections` | integer | `200` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_CONNECTIONS` |  |
| `broker.resilience.upstream.max_keepalive` | integer | `50` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_KEEPALIVE` |  |
| `broker.resilience.upstream.max_per_host` | integer | `50` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_PER_HOST` |  |
| `broker.resilience.upstream.max_request_bytes` | integer | `10485760` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_REQUEST_BYTES` |  |
| `broker.resilience.upstream.max_request_bytes_by_type` | map of integer | — | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_REQUEST_BYTES_BY_TYPE` |  |
| `broker.resilience.upstream.max_response_bytes` | integer | `10485760` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__MAX_RESPONSE_BYTES` |  |
| `broker.resilience.upstream.stream_passthrough_enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__STREAM_PASSTHROUGH_ENABLED` |  |
| `broker.resilience.upstream.transfer_deadline_s` | number | `300.0` | `JENTIC__BROKER__RESILIENCE__UPSTREAM__TRANSFER_DEADLINE_S` |  |
| `broker.resilience.backend.backend` | "memory" \| "redis" | `"memory"` | `JENTIC__BROKER__RESILIENCE__BACKEND__BACKEND` | Which shared-state implementation to build. Defined here as the single source of truth; later PRs (resilience §05) import it from this package rather than defining a second enum. |
| `broker.resilience.backend.redis_url` | string (secret) \| null | `null` | `JENTIC__BROKER__RESILIENCE__BACKEND__REDIS_URL` |  |
| `broker.resilience.backend.redis_key_prefix` | string | `"jentic:broker:"` | `JENTIC__BROKER__RESILIENCE__BACKEND__REDIS_KEY_PREFIX` |  |
| `broker.resilience.rate_limit.enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__ENABLED` |  |
| `broker.resilience.rate_limit.default_rpm` | integer | `600` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__DEFAULT_RPM` |  |
| `broker.resilience.rate_limit.burst` | integer | `100` | `JENTIC__BROKER__RESILIENCE__RATE_LIMIT__BURST` |  |
| `broker.resilience.circuit_breaker.enabled` | boolean | `true` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__ENABLED` |  |
| `broker.resilience.circuit_breaker.enforcement_mode` | "blocking" \| "observation" | `"blocking"` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__ENFORCEMENT_MODE` |  |
| `broker.resilience.circuit_breaker.failure_ratio` | number | `0.5` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__FAILURE_RATIO` |  |
| `broker.resilience.circuit_breaker.min_calls` | integer | `10` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__MIN_CALLS` |  |
| `broker.resilience.circuit_breaker.window_s` | integer | `30` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__WINDOW_S` |  |
| `broker.resilience.circuit_breaker.cooldown_s` | integer | `15` | `JENTIC__BROKER__RESILIENCE__CIRCUIT_BREAKER__COOLDOWN_S` |  |
| `broker.resilience.retry.enabled` | boolean | `false` | `JENTIC__BROKER__RESILIENCE__RETRY__ENABLED` |  |
| `broker.resilience.retry.max_attempts` | integer | `3` | `JENTIC__BROKER__RESILIENCE__RETRY__MAX_ATTEMPTS` |  |
| `broker.resilience.retry.base_backoff_s` | number | `0.2` | `JENTIC__BROKER__RESILIENCE__RETRY__BASE_BACKOFF_S` |  |
| `broker.resilience.retry.max_backoff_s` | number | `5.0` | `JENTIC__BROKER__RESILIENCE__RETRY__MAX_BACKOFF_S` |  |
| `broker.resilience.retry.retry_statuses` | list of integer | — | `JENTIC__BROKER__RESILIENCE__RETRY__RETRY_STATUSES` |  |
| `broker.idempotency.enabled` | boolean | `true` | `JENTIC__BROKER__IDEMPOTENCY__ENABLED` |  |
| `broker.idempotency.ttl_s` | number | `86400.0` | `JENTIC__BROKER__IDEMPOTENCY__TTL_S` |  |
| `broker.idempotency.pending_ttl_s` | number | `35.0` | `JENTIC__BROKER__IDEMPOTENCY__PENDING_TTL_S` |  |
| `broker.idempotency.max_response_bytes` | integer | `262144` | `JENTIC__BROKER__IDEMPOTENCY__MAX_RESPONSE_BYTES` |  |
| `broker.egress.allowed_private_subnets` | list of string | — | `JENTIC__BROKER__EGRESS__ALLOWED_PRIVATE_SUBNETS` |  |
| `broker.egress.allowed_internal_domains` | list of string | — | `JENTIC__BROKER__EGRESS__ALLOWED_INTERNAL_DOMAINS` |  |
| `broker.egress.dns_pinning_enabled` | boolean | `true` | `JENTIC__BROKER__EGRESS__DNS_PINNING_ENABLED` |  |

## `control`

Control surface configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `control.access_requests.ttl_days` | integer | `7` | `JENTIC__CONTROL__ACCESS_REQUESTS__TTL_DAYS` |  |
| `control.access_requests.canonical_base_url` | string | `""` | `JENTIC__CONTROL__ACCESS_REQUESTS__CANONICAL_BASE_URL` |  |

## `ingest`

Spec ingestion settings (fetch limits, timeouts, egress policy).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `ingest.max_spec_bytes` | integer | `26214400` | `JENTIC__INGEST__MAX_SPEC_BYTES` |  |
| `ingest.fetch_timeout_s` | number | `30.0` | `JENTIC__INGEST__FETCH_TIMEOUT_S` |  |
| `ingest.max_redirects` | integer | `5` | `JENTIC__INGEST__MAX_REDIRECTS` |  |
| `ingest.egress.allowed_private_subnets` | list of string | — | `JENTIC__INGEST__EGRESS__ALLOWED_PRIVATE_SUBNETS` |  |
| `ingest.egress.allowed_internal_domains` | list of string | — | `JENTIC__INGEST__EGRESS__ALLOWED_INTERNAL_DOMAINS` |  |
| `ingest.egress.dns_pinning_enabled` | boolean | `true` | `JENTIC__INGEST__EGRESS__DNS_PINNING_ENABLED` |  |

## `catalog`

Public API catalog settings (manifest source + staleness).

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `catalog.manifest_url` | string | `"https://raw.githubusercontent.com/jentic/jentic-public-a…` | `JENTIC__CATALOG__MANIFEST_URL` |  |
| `catalog.manifest_max_age_seconds` | integer | `86400` | `JENTIC__CATALOG__MANIFEST_MAX_AGE_SECONDS` |  |
| `catalog.update_check_interval_seconds` | integer | `86400` | `JENTIC__CATALOG__UPDATE_CHECK_INTERVAL_SECONDS` |  |
| `catalog.update_sweep_deadline_seconds` | integer | `300` | `JENTIC__CATALOG__UPDATE_SWEEP_DEADLINE_SECONDS` |  |
| `catalog.update_sweep_max_concurrency` | integer | `4` | `JENTIC__CATALOG__UPDATE_SWEEP_MAX_CONCURRENCY` |  |
| `catalog.update_sweep_jitter_ratio` | number (>= 0.0, <= 1.0) | `0.15` | `JENTIC__CATALOG__UPDATE_SWEEP_JITTER_RATIO` |  |

## `credentials`

Credentials subsystem configuration.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `credentials.encryption.active_id` | string | `"v1"` | `JENTIC__CREDENTIALS__ENCRYPTION__ACTIVE_ID` |  |
| `credentials.encryption.entries` | list of EncryptionKey | — | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES` |  |
| `credentials.encryption.entries.<n>.id` | string | *required* | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__ID` |  |
| `credentials.encryption.entries.<n>.material` | string (secret) | *required* | `JENTIC__CREDENTIALS__ENCRYPTION__ENTRIES__<N>__MATERIAL` |  |
| `credentials.providers` | map of DirectOAuth2ProviderConfig \| PipedreamProviderConfig | — | `JENTIC__CREDENTIALS__PROVIDERS` |  |
| `credentials.providers.<name>.kind` | "direct_oauth2" \| "pipedream" | `"direct_oauth2"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__KIND` |  |
| `credentials.providers.<name>.redirect_uri` | string | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__REDIRECT_URI` |  |
| `credentials.providers.<name>.default_scopes` | list of string | — | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__DEFAULT_SCOPES` |  |
| `credentials.providers.<name>.expiry_skew_seconds` | integer | `60` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__EXPIRY_SKEW_SECONDS` |  |
| `credentials.providers.<name>.authorize_extra_params` | map of string | — | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__AUTHORIZE_EXTRA_PARAMS` |  |
| `credentials.providers.<name>.project_id` | string | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__PROJECT_ID` |  |
| `credentials.providers.<name>.environment` | "production" \| "development" | `"production"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__ENVIRONMENT` |  |
| `credentials.providers.<name>.client_id` | string | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CLIENT_ID` |  |
| `credentials.providers.<name>.client_secret` | string (secret) | *required* | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CLIENT_SECRET` |  |
| `credentials.providers.<name>.connect_base_url` | string | `"https://api.pipedream.com/v1"` | `JENTIC__CREDENTIALS__PROVIDERS__<NAME>__CONNECT_BASE_URL` |  |
| `credentials.connect.state_secret` | string (secret) | `"**********"` | `JENTIC__CREDENTIALS__CONNECT__STATE_SECRET` |  |
| `credentials.connect.state_ttl_seconds` | integer | `600` | `JENTIC__CREDENTIALS__CONNECT__STATE_TTL_SECONDS` |  |

## `search`

Search configuration. The built-in mode is "lexical" (BM25 on SQLite, native full-text on PostgreSQL). `search_mode` is validated against the registered SearchStrategy set at resolve time (`resolve_strategy`), so an unknown mode fails loudly with the available modes for the active dialect rather than at config load. Additional modes (e.g. "semantic", "vector") can be registered via `register_strategy` without editing this schema.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `search.enabled` | boolean | `true` | `JENTIC__SEARCH__ENABLED` |  |
| `search.search_enabled` | boolean | `true` | `JENTIC__SEARCH__SEARCH_ENABLED` |  |
| `search.search_mode` | string | `"lexical"` | `JENTIC__SEARCH__SEARCH_MODE` |  |

## `security`

Platform security event thresholds.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `security.auth_failure_event_threshold` | integer (>= 1) | `10` | `JENTIC__SECURITY__AUTH_FAILURE_EVENT_THRESHOLD` |  |
| `security.credential_expiring_soon_window_h` | integer (>= 1) | `72` | `JENTIC__SECURITY__CREDENTIAL_EXPIRING_SOON_WINDOW_H` |  |
| `security.credential_expiry_sweep_interval_ticks` | integer (>= 1) | `60` | `JENTIC__SECURITY__CREDENTIAL_EXPIRY_SWEEP_INTERVAL_TICKS` |  |
| `security.execution_repeated_failure_threshold` | integer (>= 1) | `5` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_THRESHOLD` |  |
| `security.execution_repeated_failure_window_s` | integer (>= 1) | `300` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_WINDOW_S` |  |
| `security.execution_repeated_failure_critical_threshold` | integer (>= 1) | `20` | `JENTIC__SECURITY__EXECUTION_REPEATED_FAILURE_CRITICAL_THRESHOLD` |  |

## `telemetry`

Anonymous product-telemetry settings (issue #446). Defaults to **OFF**: an instance whose config omits this block (non-onboarded or hand-rolled) sends nothing. The onboarding CLI writes `enabled` explicitly (a yes-default `[Y]/n` prompt) so the on-by-default UX lives in the prompt, not the code default. `instance_id` seeds the durable admin-DB identity row on first startup for opted-in instances. `host_os` is the operator's OS family, stamped by the CLI at install time so a Docker-run instance reports the host's OS rather than the container's; sent once per boot, on the `instance_booted` event.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `telemetry.enabled` | boolean | `false` | `JENTIC__TELEMETRY__ENABLED` |  |
| `telemetry.instance_id` | string \| null | `null` | `JENTIC__TELEMETRY__INSTANCE_ID` |  |
| `telemetry.host_os` | string \| null | `null` | `JENTIC__TELEMETRY__HOST_OS` |  |
| `telemetry.endpoint` | string | `"https://api.jentic.com/api/v1"` | `JENTIC__TELEMETRY__ENDPOINT` |  |
| `telemetry.flush_interval_s` | number | `30.0` | `JENTIC__TELEMETRY__FLUSH_INTERVAL_S` |  |
| `telemetry.max_batch` | integer | `100` | `JENTIC__TELEMETRY__MAX_BATCH` |  |
| `telemetry.queue_max` | integer | `10000` | `JENTIC__TELEMETRY__QUEUE_MAX` |  |
| `telemetry.request_timeout_s` | number | `5.0` | `JENTIC__TELEMETRY__REQUEST_TIMEOUT_S` |  |

## `release_check`

"Update available" check for the running jentic-one build itself. Powers `GET /system/version`: the backend asks GitHub for the newest published release of `repo` and compares it against the running build so the web console can surface an "update available" banner (and the user menu can always show the current version). This is about *jentic-one's own* release — distinct from `CatalogConfig`, which tracks the public *API catalog*. Runs only on a `local` backend (a self-hosted install the operator can actually update); the hosted platform (`server.backend == "remote"`) skips it. The result is cached in-process for `cache_ttl_seconds` (fetch-on-read, no background job), so at most one GitHub request happens per TTL regardless of how many clients poll. Every failure degrades to "latest unknown" (no banner) rather than erroring — the version probe must never break the app.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `release_check.enabled` | boolean | `true` | `JENTIC__RELEASE_CHECK__ENABLED` |  |
| `release_check.repo` | string | `"jentic/jentic-one"` | `JENTIC__RELEASE_CHECK__REPO` |  |
| `release_check.cache_ttl_seconds` | integer | `21600` | `JENTIC__RELEASE_CHECK__CACHE_TTL_SECONDS` |  |

## `entitlement`

AWS Marketplace license gate for the Marketplace-listed deployment. Powers the entitlement checker (`integrations/aws_marketplace`): on startup — and every `refresh_interval_seconds` after — the process asks AWS whether this deployment's Marketplace subscription is still active, and locks the HTTP surface (503, health excepted) when it definitively is not. Defaults to **OFF**: a non-Marketplace install that omits this block runs exactly as before — nothing is wired, no AWS call is ever made. Failure posture: an *unreachable* or *erroring* AWS API is never grounds for lockout by itself — the last definitive verdict holds for `grace_period_seconds` before the gate fails closed. Only an explicit "not entitled" answer from AWS locks out immediately.

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `entitlement.enabled` | boolean | `false` | `JENTIC__ENTITLEMENT__ENABLED` |  |
| `entitlement.product_code` | string \| null | `null` | `JENTIC__ENTITLEMENT__PRODUCT_CODE` |  |
| `entitlement.region` | string | `"us-east-1"` | `JENTIC__ENTITLEMENT__REGION` |  |
| `entitlement.pricing_model` | "usage" \| "contract" | `"contract"` | `JENTIC__ENTITLEMENT__PRICING_MODEL` |  |
| `entitlement.refresh_interval_seconds` | integer | `3600` | `JENTIC__ENTITLEMENT__REFRESH_INTERVAL_SECONDS` |  |
| `entitlement.grace_period_seconds` | integer | `86400` | `JENTIC__ENTITLEMENT__GRACE_PERIOD_SECONDS` |  |
| `entitlement.license_sku` | string \| null | `null` | `JENTIC__ENTITLEMENT__LICENSE_SKU` |  |
| `entitlement.license_dimensions` | list of string | — | `JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS` |  |
| `entitlement.endpoint` | string \| null | `null` | `JENTIC__ENTITLEMENT__ENDPOINT` |  |

## `apps`

| Key | Type | Default | Env var | Description |
| --- | ---- | ------- | ------- | ----------- |
| `apps` | list of string | — | `JENTIC__APPS` |  |
