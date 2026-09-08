// API facade — feature modules import from `@/shared/api`, never from
// `./generated` directly, so the Bearer-JWT client config is always applied.
// This file is APPEND-ONLY: add re-exports, never edit/remove existing lines,
// so parallel feature PRs don't collide here.

// Client config + auth-error helper (side-effect import configures generated client).
export { ApiError, isAuthError, isClientError } from '@/shared/api/client';

// Bearer-JWT token store.
export { getToken, setToken, clearToken, subscribeToken } from '@/shared/api/token-store';

// Health (deploy-mode aware).
export { getHealth } from '@/shared/api/health';
export type { Health } from '@/shared/api/health';

// Generated typed services (regenerate with `npm run codegen`). The former
// coarse `AdminService` was split per-tag by the retag; its endpoints now live
// on UsersService / EventsService / ExecutionsService / AuditService /
// JobsService / SystemService.
export { UsersService } from '@/shared/api/generated/services/UsersService';
export { EventsService } from '@/shared/api/generated/services/EventsService';
export { ExecutionsService } from '@/shared/api/generated/services/ExecutionsService';
export { AuditService } from '@/shared/api/generated/services/AuditService';
export { JobsService } from '@/shared/api/generated/services/JobsService';
export { SystemService } from '@/shared/api/generated/services/SystemService';

// Toolkits domain. Toolkit CRUD lives on `ToolkitsService`;
// keys / credential bindings / permission rules were split per-tag into
// Toolkit{Keys,Credentials,Permissions}Service; agent-side toolkit bindings
// live on `AgentsService` (the /agents router).
export { ToolkitsService } from '@/shared/api/generated/services/ToolkitsService';
export { ToolkitKeysService } from '@/shared/api/generated/services/ToolkitKeysService';
export { ToolkitCredentialsService } from '@/shared/api/generated/services/ToolkitCredentialsService';
export { ToolkitPermissionsService } from '@/shared/api/generated/services/ToolkitPermissionsService';
export { AgentsService } from '@/shared/api/generated/services/AgentsService';
export { ServiceAccountsService } from '@/shared/api/generated/services/ServiceAccountsService';
export { AgentRegistrationService } from '@/shared/api/generated/services/AgentRegistrationService';
export type { ToolkitResponse } from '@/shared/api/generated/models/ToolkitResponse';
export type { ToolkitListResponse } from '@/shared/api/generated/models/ToolkitListResponse';
export type { ToolkitCreateRequest } from '@/shared/api/generated/models/ToolkitCreateRequest';
export type { ToolkitCreateResponse } from '@/shared/api/generated/models/ToolkitCreateResponse';
export type { ToolkitUpdateRequest } from '@/shared/api/generated/models/ToolkitUpdateRequest';
export type { ToolkitKeyResponse } from '@/shared/api/generated/models/ToolkitKeyResponse';
export type { ToolkitKeyListResponse } from '@/shared/api/generated/models/ToolkitKeyListResponse';
export type { ToolkitKeyCreateRequest } from '@/shared/api/generated/models/ToolkitKeyCreateRequest';
export type { ToolkitKeyCreateResponse } from '@/shared/api/generated/models/ToolkitKeyCreateResponse';
export type { ToolkitKeyUpdateRequest } from '@/shared/api/generated/models/ToolkitKeyUpdateRequest';
export type { ToolkitCredentialBindingResponse } from '@/shared/api/generated/models/ToolkitCredentialBindingResponse';
export type { ToolkitCredentialListResponse } from '@/shared/api/generated/models/ToolkitCredentialListResponse';
export type { ToolkitCredentialBindRequest } from '@/shared/api/generated/models/ToolkitCredentialBindRequest';
export type { ToolkitAgentResponse } from '@/shared/api/generated/models/ToolkitAgentResponse';
export type { ToolkitAgentListResponse } from '@/shared/api/generated/models/ToolkitAgentListResponse';
export type { PermissionRuleReadSchema } from '@/shared/api/generated/models/PermissionRuleReadSchema';
// The codegen retag split the single `PermissionRuleSchema` model into two
// tag-namespaced variants (access-requests vs toolkits). Toolkit permission
// rules use the toolkit variant; re-export it under the stable public name so
// downstream consumers stay unchanged.
export type { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema as PermissionRuleSchema } from '@/shared/api/generated/models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
export { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema as PermissionRuleSchemaNS } from '@/shared/api/generated/models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
export type { PermissionRuleListResponse } from '@/shared/api/generated/models/PermissionRuleListResponse';
export type { PermissionsPatchRequest } from '@/shared/api/generated/models/PermissionsPatchRequest';
export type { PermissionTestRequest } from '@/shared/api/generated/models/PermissionTestRequest';
export type { PermissionTestResponse } from '@/shared/api/generated/models/PermissionTestResponse';
export type { BindingWarningSchema } from '@/shared/api/generated/models/BindingWarningSchema';
export type { ToolkitBindingResponse } from '@/shared/api/generated/models/ToolkitBindingResponse';
export type { ToolkitBindingListResponse } from '@/shared/api/generated/models/ToolkitBindingListResponse';
export type { ToolkitBindRequest } from '@/shared/api/generated/models/ToolkitBindRequest';
// Audit (read-only, toolkit-scoped lens on the shared /audit endpoint via AuditService).
export type { AuditResponse } from '@/shared/api/generated/models/AuditResponse';
export type { AuditListResponse } from '@/shared/api/generated/models/AuditListResponse';
export { AuditTargetType } from '@/shared/api/generated/models/AuditTargetType';

// Generated models used by the auth/foundation layer.
export type { LoginRequest } from '@/shared/api/generated/models/LoginRequest';
export type { LoginResponse } from '@/shared/api/generated/models/LoginResponse';
export type { CurrentUserResponse } from '@/shared/api/generated/models/CurrentUserResponse';
export type { ChangePasswordRequest } from '@/shared/api/generated/models/ChangePasswordRequest';
export type { RedeemInviteRequest } from '@/shared/api/generated/models/RedeemInviteRequest';
export type { HealthResponse } from '@/shared/api/generated/models/HealthResponse';

// Agents / service-accounts / dynamic registration (ui-agents module).
// Note: AgentsService + ToolkitBinding* models are already exported above by
// the toolkits module; agents reuses them and does not re-export to avoid dupes.
export type { AgentCreateRequest } from '@/shared/api/generated/models/AgentCreateRequest';
export type { AgentResponse } from '@/shared/api/generated/models/AgentResponse';
export type { ApiKeyResponse } from '@/shared/api/generated/models/ApiKeyResponse';
export type { ApiKeyInfoResponse } from '@/shared/api/generated/models/ApiKeyInfoResponse';
export type { ApiKeyHistoryResponse } from '@/shared/api/generated/models/ApiKeyHistoryResponse';
export type { ApiKeyHistoryEntryResponse } from '@/shared/api/generated/models/ApiKeyHistoryEntryResponse';
export type { AgentListResponse } from '@/shared/api/generated/models/AgentListResponse';
export type { ServiceAccountResponse } from '@/shared/api/generated/models/ServiceAccountResponse';
export type { ServiceAccountListResponse } from '@/shared/api/generated/models/ServiceAccountListResponse';
export type { ServiceAccountCreateRequest } from '@/shared/api/generated/models/ServiceAccountCreateRequest';
export type { RegisterRequest } from '@/shared/api/generated/models/RegisterRequest';
export type { RegisterResponse } from '@/shared/api/generated/models/RegisterResponse';
export type { jentic_one__auth__web__schemas__agents__DenyRequest as AgentDenyRequest } from '@/shared/api/generated/models/jentic_one__auth__web__schemas__agents__DenyRequest';
export type { jentic_one__auth__web__schemas__service_accounts__DenyRequest as ServiceAccountDenyRequest } from '@/shared/api/generated/models/jentic_one__auth__web__schemas__service_accounts__DenyRequest';

// Agent Rail — the persistent live-event rail consumes the REAL platform event
// feed (`/events` + `/events/stream` SSE) via `EventsService`. These models
// are already generated; re-exported here
// (append-only) so the rail's data layer in `shared/lib/agentStream` can stay
// behind the facade like every other module.
export type { EventResponse } from '@/shared/api/generated/models/EventResponse';
export type { EventListResponse } from '@/shared/api/generated/models/EventListResponse';
export type { EventLinks } from '@/shared/api/generated/models/EventLinks';
export type { EventAcknowledgeRequest } from '@/shared/api/generated/models/EventAcknowledgeRequest';
export { EventSeverity } from '@/shared/api/generated/models/EventSeverity';

// Discover (catalog) slice — services + models. The former `ApisService` was
// renamed `ApIsService` by the retag (+ ApiSpecService / ApiOperationsService).
export { CatalogService } from '@/shared/api/generated/services/CatalogService';
export { ApIsService } from '@/shared/api/generated/services/ApIsService';
export { ApiSpecService } from '@/shared/api/generated/services/ApiSpecService';
export { ApiOperationsService } from '@/shared/api/generated/services/ApiOperationsService';
export type { CatalogListResponse } from '@/shared/api/generated/models/CatalogListResponse';
export type { CatalogEntryResponse } from '@/shared/api/generated/models/CatalogEntryResponse';
export type { CatalogEntryLinksResponse } from '@/shared/api/generated/models/CatalogEntryLinksResponse';
export type { CatalogRefreshResponse } from '@/shared/api/generated/models/CatalogRefreshResponse';
export type { OperationPreviewListResponse } from '@/shared/api/generated/models/OperationPreviewListResponse';
export type { PreviewOperationResponse } from '@/shared/api/generated/models/PreviewOperationResponse';
export type { PreviewInfoResponse } from '@/shared/api/generated/models/PreviewInfoResponse';
export type { PreviewParameterResponse } from '@/shared/api/generated/models/PreviewParameterResponse';
export type { ApiImportResponse } from '@/shared/api/generated/models/ApiImportResponse';
export type { ApiListResponse } from '@/shared/api/generated/models/ApiListResponse';
export type { ApiResponse } from '@/shared/api/generated/models/ApiResponse';
export type { ApiReferenceResponse } from '@/shared/api/generated/models/ApiReferenceResponse';
export type { ApiLinksResponse } from '@/shared/api/generated/models/ApiLinksResponse';

// [ui-dashboard] Execution models the overview renders. The Event* models
// (EventResponse/EventListResponse/EventSeverity) the Dashboard also needs are
// already exported above by the Agent Rail block; re-exporting them here would
// be a duplicate, so Dashboard reuses those and only adds the Execution models.
export type { ExecutionResponse } from '@/shared/api/generated/models/ExecutionResponse';
export type { ExecutionListResponse } from '@/shared/api/generated/models/ExecutionListResponse';

// Credentials module.
export { CredentialsService } from '@/shared/api/generated/services/CredentialsService';
export { CredentialType } from '@/shared/api/generated/models/CredentialType';
export { CredentialLocation } from '@/shared/api/generated/models/CredentialLocation';
export type { ApiKeyCreateRequest } from '@/shared/api/generated/models/ApiKeyCreateRequest';
export type { ApiKeyUpdateRequest } from '@/shared/api/generated/models/ApiKeyUpdateRequest';
export type { BasicAuthCreateRequest } from '@/shared/api/generated/models/BasicAuthCreateRequest';
export type { BasicAuthUpdateRequest } from '@/shared/api/generated/models/BasicAuthUpdateRequest';
export type { BearerTokenCreateRequest } from '@/shared/api/generated/models/BearerTokenCreateRequest';
export type { BearerTokenUpdateRequest } from '@/shared/api/generated/models/BearerTokenUpdateRequest';
export type { OAuth2CreateRequest } from '@/shared/api/generated/models/OAuth2CreateRequest';
export type { OAuth2UpdateRequest } from '@/shared/api/generated/models/OAuth2UpdateRequest';
export type { Sigv4CreateRequest } from '@/shared/api/generated/models/Sigv4CreateRequest';
export type { Sigv4UpdateRequest } from '@/shared/api/generated/models/Sigv4UpdateRequest';
export type { APIReference } from '@/shared/api/generated/models/APIReference';
export type { APIReferenceRequest } from '@/shared/api/generated/models/APIReferenceRequest';
export type { RuntimeConfig } from '@/shared/api/generated/models/RuntimeConfig';
export type { ConnectRequestBody } from '@/shared/api/generated/models/ConnectRequestBody';
export type { ConnectChallengeResponse } from '@/shared/api/generated/models/ConnectChallengeResponse';
export type { CredentialCreateResponse } from '@/shared/api/generated/models/CredentialCreateResponse';
export type { CredentialListResponse } from '@/shared/api/generated/models/CredentialListResponse';
export type { CredentialRedactedResponse } from '@/shared/api/generated/models/CredentialRedactedResponse';
export type { ProviderDiscoveryResponse } from '@/shared/api/generated/models/ProviderDiscoveryResponse';
export type { ProviderDiscoveryEntryResponse } from '@/shared/api/generated/models/ProviderDiscoveryEntryResponse';

// Agent Rail — access-request decisions (`POST /access-requests/{id}:decide`).
// The access-request router (tag "Access Requests") is now exposed as a
// generated `AccessRequestsService` after the codegen retag. The rail's
// access-request repository (`shared/lib/accessRequests`) still issues its calls
// through the low-level request primitive the generated services use, kept
// behind the facade so the Bearer-JWT `OpenAPI` config still applies; switching
// it to `AccessRequestsService` is a safe follow-up. Append-only, like the rest.
export { AccessRequestsService } from '@/shared/api/generated/services/AccessRequestsService';
export { OpenAPI } from '@/shared/api/generated/core/OpenAPI';
export { request as apiRequest } from '@/shared/api/generated/core/request';

// First-run setup. The one-time create-admin
// endpoint bootstraps the first operator account; CreateAdminRequest is its body.
export type { CreateAdminRequest } from '@/shared/api/generated/models/CreateAdminRequest';
// Shared react-query key for the first-run health/setup probe (SetupGate reads
// it; the create-admin flow invalidates it). Kept here so reader and invalidator
// import the same constant and can't drift.
export { HEALTH_QUERY_KEY } from '@/shared/api/health';

// Cross-module query-key registry (#511). Owns the few cache-key roots that
// cross a module boundary so each is defined once; a module invalidates a
// sibling's cache through this instead of a hand-synced raw key literal.
export { sharedQueryKeys } from '@/shared/api/queryKeys';

// Actor scopes (#615). The platform permission catalogue + the agent/service-
// account scope grant endpoints. `AgentsService`/`ServiceAccountsService` are
// already exported above (toolkits/agents blocks); these add the permission
// catalogue service and the scope request/response models the agents module
// wires into the Scopes card. Append-only.
export { PermissionsService } from '@/shared/api/generated/services/PermissionsService';
export type { PermissionResponse } from '@/shared/api/generated/models/PermissionResponse';
export type { PermissionListResponse } from '@/shared/api/generated/models/PermissionListResponse';
export type { AgentScopesRequest } from '@/shared/api/generated/models/AgentScopesRequest';
export type { AgentScopesResponse } from '@/shared/api/generated/models/AgentScopesResponse';
export type { ServiceAccountScopesRequest } from '@/shared/api/generated/models/ServiceAccountScopesRequest';
export type { ServiceAccountScopesResponse } from '@/shared/api/generated/models/ServiceAccountScopesResponse';

// --- Monitor module (executions / jobs / events / audit) -------------------
// Re-exported through the facade so the Monitor repository tier consumes typed
// services + models from `@/shared/api` rather than reaching into `./generated`
// (ESLint layering forbids the latter). The retag split the old coarse
// AdminService into per-tag services: Monitor's tabs use ExecutionsService /
// JobsService / EventsService / AuditService — all exported above.
// Note: Execution*, Event*, and Audit* models are already exported above (by the
// dashboard, agent-rail, and toolkits blocks respectively); Monitor reuses them.
// Only the Job models are not yet re-exported, so add them here (append-only).
export type { JobResponse } from '@/shared/api/generated/models/JobResponse';
export type { JobListResponse } from '@/shared/api/generated/models/JobListResponse';

// Monitor Overview parity: the enriched usage-aggregation endpoint
// (GET /monitoring/usage, `MonitoringService.getUsageStats`).
// Powers the Monitor Overview — bubble chart, per-row sparkline
// trends, latency pills, and the api/toolkit/agent grouping toggle. `GroupBy`
// is exported as a *value* because callers pass the enum members as the
// `group_by` query param.
export { MonitoringService } from '@/shared/api/generated/services/MonitoringService';
export { GroupBy } from '@/shared/api/generated/models/GroupBy';
export type { UsageResponse } from '@/shared/api/generated/models/UsageResponse';
export type { UsageStatsBlock } from '@/shared/api/generated/models/UsageStatsBlock';
export type { UsageBucket } from '@/shared/api/generated/models/UsageBucket';
export type { UsageTopRow } from '@/shared/api/generated/models/UsageTopRow';

// Monitor global filter bar: the actor directory (GET /actors,
// `ActorsService.listActors`) hydrates the actor picker shared across the
// Executions/Events/Audit tabs. Also consumed by the shared actor-directory
// hook (`useActorDirectory`) + `<ActorLabel>` to resolve raw `actor_id` values
// into human-readable names across access-request and agent surfaces.
// `ActorType` is exported as a *value* (not just a type) because `<ActorLabel>`
// reads the enum members for its subtle type prefix. Append-only.
export { ActorsService } from '@/shared/api/generated/services/ActorsService';
export { ActorType } from '@/shared/api/generated/models/ActorType';
export type { ActorListResponse } from '@/shared/api/generated/models/ActorListResponse';
export type { ActorSummaryResponse } from '@/shared/api/generated/models/ActorSummaryResponse';

// Session lifecycle (#610/#608): expiry-aware token adoption + proactive
// refresh scheduling + the one-shot "session expired" login notice.
export {
	setSession,
	getSessionExpiresAt,
	consumeSessionExpiredNotice,
} from '@/shared/api/token-store';
export type { ClearTokenReason } from '@/shared/api/token-store';

// Overlays (#937). The overlay lifecycle
// service backing the workspace OverlaysSection + the "close the overlay-update
// loop" flow (list/get/confirm/rollback/deprecate). List/get responses are
// typed `any` on the generated client; the workspace module re-types them in
// its own `api/types.ts` + `adapters.ts`. Append-only, like the rest.
export { OverlaysService } from '@/shared/api/generated/services/OverlaysService';

// System version. The running vs. latest-
// available app release, read by the shell's update banner + UserMenu version
// line via `useVersionInfo`. `SystemService` is already exported above; only the
// response model is added here. Append-only.
export type { VersionResponse } from '@/shared/api/generated/models/VersionResponse';

// OAuth client management (third-party application registrations). Append-only.
export { OAuthClientsService } from '@/shared/api/generated/services/OAuthClientsService';
export type { OAuthClientCreateRequest } from '@/shared/api/generated/models/OAuthClientCreateRequest';
export type { OAuthClientCreateResponse } from '@/shared/api/generated/models/OAuthClientCreateResponse';
export type { OAuthClientResponse } from '@/shared/api/generated/models/OAuthClientResponse';
export type { OAuthClientRotateSecretResponse } from '@/shared/api/generated/models/OAuthClientRotateSecretResponse';
export type { OAuthClientUpdateRequest } from '@/shared/api/generated/models/OAuthClientUpdateRequest';

// Instance identity (local-MCP 2-E2, #1188). `GET /instance` self-describes
// the backend (canonical base URL / host / locality) so the per-agent MCP
// config card can show which instance a pasted snippet will talk to.
// `SystemService` is already exported above; only the response model is added
// here. Append-only.
export type { InstanceIdentityResponse } from '@/shared/api/generated/models/InstanceIdentityResponse';

// External-IdP (SSO) login. The public capability descriptor (`GET /auth/idp`)
// tells the login page whether to show a "Continue with <provider>" button, and
// the authorization-code + PKCE exchange (`POST /oauth/token`) yields the same
// session bundle as password login. Append-only, like the rest.
export { getIdpDescriptor, exchangeAuthCode } from '@/shared/api/idp';
export type { IdpDescriptor } from '@/shared/api/idp';

// OAuth consent grants. The per-agent
// "Connected clients" listing lives on `AgentsService.listAgentOauthGrants`
// (already exported above); the grant kill switch (`POST
// /oauth-grants/{id}:revoke`) and the admin cross-view (`GET
// /admin/oauth-grants`) live on `OAuthService`. Append-only, like the rest.
export { OAuthService } from '@/shared/api/generated/services/OAuthService';
export type { OAuthGrantResponse } from '@/shared/api/generated/models/OAuthGrantResponse';
export type { OAuthGrantListResponse } from '@/shared/api/generated/models/OAuthGrantListResponse';
