# Issue #1556 integration branch: conflict resolutions

Integration branch `test/issue-1556-e2e`, off `origin/main` at c01db60c.
Merged with `git merge --no-ff` in this order: phase-0 (#1558), 4a-i (#1563),
phase-2 (#1559), phase-1 (#1560), phase-3c (#1565, carries 3a/3b),
phase-4b (#1568, carries 4a-ii/4a-iii), phase-4c (#1569).

Merges 1-3 (phase-0, 4a-i, phase-2) were clean. Generated artifacts were not
hand-merged: one side was taken, then regenerated (`UPDATE_MCP_SPEC=1 go test
./internal/cli/api` and a copy to `src/jentic_one/mcp/_spec/`, `go run
./cmd/clidocs`, `make skills`). `make openapi`, `make endpoints`,
`make config-reference`, `make broker-reference`, `cli: make generate-api`
and `ui: npm run codegen` produced no drift after the merges.
`cli: make generate-config` did produce drift (see fixes).

## Merge 4: feat/issue-1556-phase-1 (#1560) into main+0+4a-i+2
- src/jentic_one/mcp/tools.py (AmbiguousVendorError arm): took #1559 (candidates via VendorRegistryService, _ambiguous_vendor_actionable, next_tool=request_connection, InvalidOAuthAppRegistrationError arm). Dropped #1560's "ask operator / no next_tool" text — #1559 supersedes it (agent asks user, retries with pin).
- tests/unit/mcp/test_tools_request_connection.py: took #1559's tests (pin/rules/importer/malformed-rules); dropped #1560's `next_tool not in payload` assertion.
- cli/internal/cli/api/mcp_request_connection_test.go: took #1559's next_tool=request_connection + candidates assertions; dropped #1560's get_started expectation.

## Merge 5: feat/issue-1556-phase-3c (#1565, carries 3a+3b) 
- src/jentic_one/broker/core/exceptions.py `no_credential_binding_directive`: kept #1560's `connect: ConnectTarget` + `suggested_rules` params AND #1565's `provisioning_url`. When `provisioning_url` is set (open connect session), `connect_parameters(None, suggested_rules)` — i.e. no `suggested_command` and no `parameters.connect` (3c's "don't start another connect"; `connect` would otherwise make the MCP envelope point next_tool at request_connection), but `suggested_rules` still rides.
- src/jentic_one/broker/services/credentials/orchestrator.py `_not_provisioned`: now `async` (3c) with #1560's keyword `connect`/`suggested_rules`; same precedence: provisioning_url > connect > operator prose. #1560's leftover `account_linking_base_url` block dropped (3c retired the config field). Both call sites `raise await self._not_provisioned(..., connect=await self._connect_target(api), ...)`.
- src/jentic_one/broker/services/execution/authorization.py: `_empty_credential_derivation_denial` takes connect + suggested_rules + provisioning_url; call site passes all three (#1560's resolve_connect_target replaces 3c's `_connect_vendor_for`).
- tests/unit/broker/test_credential_service.py: kept both #1560's `_Registrations`/`_service()` helpers and 3c's autouse `open_session` stub; 3c's `CredentialService(_ctx())` calls -> `_service(_ctx())`; dropped `account_linking_base_url=` kwargs from #1560 tests (field retired); open-session test also asserts `connect` absent.
- tests/unit/broker/test_problem_json.py: kept both tests; 3c's open-session test ported to `connect=ConnectTarget(...)` + `suggested_rules` and asserts `connect` absent, `suggested_rules` present.
- skills/jentic/references/{mcp,recovery}.md (+ both mirrors via `make skills`): union — #1560's connect-target/next_tool_arguments/suggested_rules prose + 3c's "provisioning_url = your pending request, relay it, don't reconnect"; added "a denial with provisioning_url omits connect and suggested_command".

## Merge 6: feat/issue-1556-phase-4b (#1568, carries 4a-ii, 4a-iii, 4a-i)
- cli/internal/cli/api/curated.go (connect NotExposed): dropped `oauth_app_registration_id` (#1559 binds it to `--registration`), kept 4b's `api` and `auth_type` entries.
- src/jentic_one/control/services/integrations/connect_session_service.py (imports): union — 3c's `connect_approval_url`, `emit_event_best_effort`/`summary_label`, `EventSeverity`/`EventType` + 4b's `ApiSecurityView`, `SecuritySchemesLookupProtocol`, `DatabaseIntegrityError`, `credential_covers`, `slugify_api_field`, `StoredCredentialType`. Dropped `resolved_auth_base_url` (3c's `_approval_url_for` now uses `connect_approval_url`; no other user).
- tests/integration/control/test_connect_session_service.py: union of imports (Event/EventService/EventFilter + ConnectSessionOutcome) and of both test sections (3c's connect_session.created rail events, 4b's target kinds / awaiting_app / outcomes).

## Merge 7: feat/issue-1556-phase-4c (#1569; sibling of 4b off 4a-iii — has neither 3c nor #1559/#1560)
Broker directives (precedence everywhere: open-session `provisioning_url` (3c) > `ConnectTarget` vendor/shared-app (#1560) > API target (4c, gated on `control.connect.manual_flows_enabled`) > operator prose):
- src/jentic_one/broker/core/exceptions.py: kept #1560's `suggested_permission_rules`/`connect_parameters` AND 4c's `api_connect_parameters`/`api_connect_hint`. `no_credential_binding_directive` takes `connect`, `suggested_rules`, `provisioning_url`, `connect_api`; API form (`suggested_command: jentic connect --api v/n/ver`, `parameters.connect = {"api": {...}}`) applies only when `connect is None` and no provisioning_url. Both connect shapes therefore coexist: `{vendor_key, registration_id?}` or `{api: {vendor,name,version}}`. `suggested_rules` rides in every case.
- orchestrator.py `_not_provisioned`: `connect_api = connect is None and gate and full identity`; dropped 4c's `account_linking_base_url` block (retired by 3c).
- authorization.py: dropped 4c's `_connect_vendor_for` (replaced by #1560 `resolve_connect_target`), kept `_api_connect_enabled`; denial gets connect + suggested_rules + provisioning_url + connect_api.
- tests/unit/broker/test_problem_json.py: kept both; 4c's "vendor wins" case ported to `connect=ConnectTarget(...)` and now asserts `connect == {"vendor_key": "github"}` (#1560 adds it); added "open session beats API target" case.
- tests/unit/broker/test_credential_service.py: `_ctx` keeps #1560/3c `public_base_url` default + 4c `manual_flows_enabled`; 4c's API-target test drops the `account_linking_base_url` parametrization and uses `_service()`.
MCP (Python):
- mcp/app.py + tools.py `CallEnv`: BOTH seams — `catalog_auto_importer` (#1559) and `security_schemes_lookup` (4c), both threaded into `ConnectSessionService(...)`.
- tools.py: params = vendor, api, auth_type, requested_scopes, reason, oauth_app_registration_id, requested_permission_rules; error imports union; kept #1559 helpers (`_ambiguous_vendor_*`, `_requested_permission_rules`) and 4c helpers (`_is_human_entry_flow`, `_connect_api_target`, `_api_target_tool_error`). Ambiguous-vendor arm = #1559 behaviour (candidates + next_tool request_connection) with 4c's `target` in the message (`vendor or exc.vendor` for the advice); InvalidOAuthAppRegistration arm kept.
- tests/unit/mcp/test_tools_request_connection.py: 4c's `**seams` fake; #1559's importer assertion rewritten against `seams`; 4c's seam assertion includes `catalog_auto_importer: None`.
Go CLI:
- connect.go: flags union (`--registration` #1559; `--api`, `--auth-type`, `--rules` 4c); Long help union; `Args: MaximumNArgs(1)`; `connectCoded(ctx, client, target, isAPI, err)` — 4c's apiTargetCoded/422/isAPI arms + #1559's candidates/invalid-registration arms, retry hints use `jentic connect [--api] <target>`.
- curated.go: Bind = vendor, api, auth_type→auth-type, requested_scopes→scopes, requested_permission_rules→rules, reason, oauth_app_registration_id→registration; NotExposed = agent_id, preferred_flow, name.
- mcp_request_connection.go: params union; `requestConnectionError(ctx, client, target, isAPI, err, retry)`; kept `connectRegistrationMax`; tool description merged (api targets + human-entry flows + rules + shared-app candidates).
- mcp_execute_test.go: kept both table rows (#1560 shared_app/action_denied, 4c api_target).
Generated: docs/reference/mcp-tools.json regenerated (`UPDATE_MCP_SPEC=1 go test ./internal/cli/api`) and copied to src/jentic_one/mcp/_spec/; ui/public/cli-reference.json regenerated (`go run ./cmd/clidocs`).
Skills (skills/jentic/references/{cli,mcp,recovery}.md, mirrored by `make skills`): union of #1559 shared-app picking, 3c pending-request relay, #1560 connect-target data, 4c `--api`/`api`/end-your-turn; `parameters.connect` documented as either shape; MCP `next_tool_arguments` documented as `{"vendor":…}` or `{"api":…}` (see F5 fix).

## Fixes made on the integration branch (one commit each)

| Commit | What | Belongs to |
| --- | --- | --- |
| chore(e2e): fill request_connection's api argument … | Both MCP mounts fill `next_tool_arguments` = `{"api": {...}}` from `parameters.connect.api` (vendor key still wins). Python `_connect_api` + Go `connectAPI`, with tests on both sides. | #1569 rebased onto #1560 (follow-up F5) |
| chore(e2e): treat awaiting_app connect sessions as open … | Broker raw-SQL open-session read listed only `created`/`polling`; added `awaiting_app` to mirror `LIVE_STATES`, plus a pin test. | #1565 rebased onto #1566 |
| chore(e2e): announce agent API-target connect requests … | `connect_session.created` rail event was emitted only on the vendor create path; the API-target path (`_create_api_session`) now emits it too. | #1567 rebased onto #1565 |
| chore(e2e): regenerate the installer config struct … | `cli/internal/cli/ctl/generated/config.go` carried #1565's hand edit; regenerated with `make generate-config` (declaration order only). | #1565 (follow-up F8) |
| test(control): type the dedupe-key map … | mypy error, already in the PR on its own. | #1566 |
| test(control): satisfy mypy in the API-target connect session tests | mypy errors, already in the PR on its own. | #1567 |

## Open points (not changed here)

- `next_tool_arguments` still leaves the directive's `connect.registration_id`
  out, though #1559 lets `request_connection` take `oauth_app_registration_id`.
- `tests/integration/control/test_service_account_migration.py::test_retire_copies_post_stamp_grants_but_never_resurrects_removed_twins`
  also fails on plain `origin/main` (checked against a fresh Postgres).
