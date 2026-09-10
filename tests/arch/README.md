# Architecture tests

Executable rules. Each file pins one architectural or drift contract; if a
test here fails, the code (or doc) broke the contract — fix the code, not the
test. Run them all with:

```bash
uv run pytest tests/arch -q
```

`_rules_facts.py` supplies the shared facts the tests assert against, with a
vendored fallback under [`vendored/`](vendored/) guarded by
[`test_rules_facts_vendored.py`](test_rules_facts_vendored.py).

## Docs and generated-artifact drift

- [`test_docs_links.py`](test_docs_links.py) — every relative link in tracked markdown resolves; every `docs/**/*.md` is reachable from `docs/README.md`
- [`test_agent_docs_refs.py`](test_agent_docs_refs.py) — `llms.txt` / `AGENTS.md` links and counts stay true
- [`test_config_reference_conformance.py`](test_config_reference_conformance.py) — `docs/reference/config.md` is byte-equal to its generator's output
- [`test_config_schema_conformance.py`](test_config_schema_conformance.py) — the config JSON Schema (and the CLI's vendored copy) hasn't drifted
- [`test_endpoint_tree.py`](test_endpoint_tree.py) — the endpoint/scope reference matches the live route table
- [`test_openapi_conformance.py`](test_openapi_conformance.py) — OpenAPI specs are well-formed and (control) not drifted
- [`test_install_docs_conformance.py`](test_install_docs_conformance.py) — documented `brew`/`winget`/`scoop` install commands match `cli/.goreleaser.yaml`
- [`test_cosign_identity_pins.py`](test_cosign_identity_pins.py) — every `--certificate-identity` (docs, `install.sh`, the Go updater) names a workflow that exists
- [`test_skill_drift.py`](test_skill_drift.py) — the served skill set stays in lockstep across its copies
- [`test_scope_catalog.py`](test_scope_catalog.py) — the conceptual scope catalogue covers every scope in use
- [`test_revision_pin_regex_matches_spec.py`](test_revision_pin_regex_matches_spec.py) — the broker revision-pin regex matches the OpenAPI spec

## Module boundaries and layering

- [`test_module_boundaries.py`](test_module_boundaries.py) — surfaces never import each other; `shared/` imports no surface
- [`test_web_layer.py`](test_web_layer.py) — web layer rules (`web → services → repos → core`)
- [`test_web_links_absolute.py`](test_web_links_absolute.py) — routers build links through `build_link`, never by hand
- [`test_broker_runner_seam.py`](test_broker_runner_seam.py) — the broker execution path stays runner-shaped, not inlined
- [`test_worker_no_inline_dispatch.py`](test_worker_no_inline_dispatch.py) — the async worker holds no inline upstream dispatch
- [`test_search_encapsulation.py`](test_search_encapsulation.py) — services and web never import search strategy modules directly
- [`test_no_ml_in_core_surfaces.py`](test_no_ml_in_core_surfaces.py) — core surfaces never import ML embedding modules
- [`test_error_handler_consistency.py`](test_error_handler_consistency.py) — MRO-walk error handling lives only in the shared module
- [`test_admin_base_usage.py`](test_admin_base_usage.py), [`test_admin_secrets_isolation.py`](test_admin_secrets_isolation.py), [`test_admin_services_no_sqlalchemy.py`](test_admin_services_no_sqlalchemy.py), [`test_admin_services_style_a.py`](test_admin_services_style_a.py) — the admin surface's model base, secrets isolation, and service style

## Single-import-point facades

- [`test_encryption_facade.py`](test_encryption_facade.py) — only `shared/crypto/` imports `cryptography`
- [`test_metrics_facade.py`](test_metrics_facade.py) — only `shared/metrics.py` imports exporter packages
- [`test_tracing_facade.py`](test_tracing_facade.py) — OTel instrumentation lives only in `shared/tracing.py`
- [`test_jwks_single_source.py`](test_jwks_single_source.py) — JWKS key operations consolidated in `shared/auth/jwks.py`
- [`test_no_stdlib_logging.py`](test_no_stdlib_logging.py) — no stdlib `logging.getLogger` in application code

## Database and ORM

- [`test_orm_conventions.py`](test_orm_conventions.py) — ORM model conventions
- [`test_no_backref.py`](test_no_backref.py) — no SQLAlchemy `backref`
- [`test_no_direct_db.py`](test_no_direct_db.py) — model modules never import DB internals directly
- [`test_no_manual_commit.py`](test_no_manual_commit.py) — no production `session.commit()`/`rollback()`
- [`test_migration_single_head.py`](test_migration_single_head.py) — exactly one alembic head per migration tree
- [`test_no_db_mocking.py`](test_no_db_mocking.py) — tests never mock database internals
- [`test_scoping_boundary.py`](test_scoping_boundary.py), [`test_scoping_coverage.py`](test_scoping_coverage.py) — repos stay auth-agnostic, every scoped model is registered, identity is mandatory in scoped surfaces

## Security, secrets, and audit

- [`test_no_static_secret_defaults.py`](test_no_static_secret_defaults.py) — no config secret ships a static default
- [`test_secrets_are_secretstr.py`](test_secrets_are_secretstr.py) — secret-like config fields are `SecretStr`
- [`test_oauth_client_secret_coupling.py`](test_oauth_client_secret_coupling.py) — the OAuth-client none↔NULL-secret-hash coupling stays pinned
- [`test_no_system_actor.py`](test_no_system_actor.py) — the removed "system" actor never comes back
- [`test_origin_propagation.py`](test_origin_propagation.py) — every audit record carries an origin
- [`test_instance_id_stable.py`](test_instance_id_stable.py) — the telemetry instance id is never regenerated

## Telemetry

- [`test_telemetry_consent_gate.py`](test_telemetry_consent_gate.py) — telemetry never sends without explicit opt-in
- [`test_telemetry_no_pii.py`](test_telemetry_no_pii.py) — the telemetry wire payload can carry no PII by construction

## Conventions

- [`test_commit_convention.py`](test_commit_convention.py) — the conventional-commit contract the commit-msg hook enforces
- [`test_no_inline_imports.py`](test_no_inline_imports.py) — all imports at module level
- [`test_no_test_classes.py`](test_no_test_classes.py) — pytest-style tests only, no `class Test*`
- [`test_service_list_naming.py`](test_service_list_naming.py) — service-layer list naming
- [`test_openapi_metadata.py`](test_openapi_metadata.py) — every generated operation carries the spec metadata catalogue
