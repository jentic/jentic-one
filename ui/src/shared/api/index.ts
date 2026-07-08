// API facade — feature modules import from `@/shared/api`, never from
// `./generated` directly, so the Bearer-JWT client config is always applied.
// This file is APPEND-ONLY: add re-exports, never edit/remove existing lines,
// so parallel feature PRs don't collide here.

// Client config + auth-error helper (side-effect import configures generated client).
export { ApiError, isAuthError, isClientError } from '@oss-internal/shared/api/client';

// Bearer-JWT token store.
export {
	getToken,
	setToken,
	clearToken,
	subscribeToken,
} from '@oss-internal/shared/api/token-store';

// Health (deploy-mode aware).
export { getHealth } from '@oss-internal/shared/api/health';
export type { Health } from '@oss-internal/shared/api/health';

// Generated typed services (regenerate with `npm run codegen`). The former
// coarse `AdminService` was split per-tag by the retag; its endpoints now live
// on UsersService / EventsService / ExecutionsService / AuditService /
// JobsService / SystemService.
export { UsersService } from '@oss-internal/shared/api/generated/services/UsersService';
export { EventsService } from '@oss-internal/shared/api/generated/services/EventsService';
export { ExecutionsService } from '@oss-internal/shared/api/generated/services/ExecutionsService';
export { AuditService } from '@oss-internal/shared/api/generated/services/AuditService';
export { JobsService } from '@oss-internal/shared/api/generated/services/JobsService';
export { SystemService } from '@oss-internal/shared/api/generated/services/SystemService';

// Toolkits domain (feat/ui-toolkits). Toolkit CRUD lives on `ToolkitsService`;
// keys / credential bindings / permission rules were split per-tag into
// Toolkit{Keys,Credentials,Permissions}Service; agent-side toolkit bindings
// live on `AgentsService` (the /agents router, formerly `AuthService`).
export { ToolkitsService } from '@oss-internal/shared/api/generated/services/ToolkitsService';
export { ToolkitKeysService } from '@oss-internal/shared/api/generated/services/ToolkitKeysService';
export { ToolkitCredentialsService } from '@oss-internal/shared/api/generated/services/ToolkitCredentialsService';
export { ToolkitPermissionsService } from '@oss-internal/shared/api/generated/services/ToolkitPermissionsService';
export { AgentsService } from '@oss-internal/shared/api/generated/services/AgentsService';
export { ServiceAccountsService } from '@oss-internal/shared/api/generated/services/ServiceAccountsService';
export { AgentRegistrationService } from '@oss-internal/shared/api/generated/services/AgentRegistrationService';
export type { ToolkitResponse } from '@oss-internal/shared/api/generated/models/ToolkitResponse';
export type { ToolkitListResponse } from '@oss-internal/shared/api/generated/models/ToolkitListResponse';
export type { ToolkitCreateRequest } from '@oss-internal/shared/api/generated/models/ToolkitCreateRequest';
export type { ToolkitCreateResponse } from '@oss-internal/shared/api/generated/models/ToolkitCreateResponse';
export type { ToolkitUpdateRequest } from '@oss-internal/shared/api/generated/models/ToolkitUpdateRequest';
export type { ToolkitKeyResponse } from '@oss-internal/shared/api/generated/models/ToolkitKeyResponse';
export type { ToolkitKeyListResponse } from '@oss-internal/shared/api/generated/models/ToolkitKeyListResponse';
export type { ToolkitKeyCreateRequest } from '@oss-internal/shared/api/generated/models/ToolkitKeyCreateRequest';
export type { ToolkitKeyCreateResponse } from '@oss-internal/shared/api/generated/models/ToolkitKeyCreateResponse';
export type { ToolkitKeyUpdateRequest } from '@oss-internal/shared/api/generated/models/ToolkitKeyUpdateRequest';
export type { ToolkitCredentialBindingResponse } from '@oss-internal/shared/api/generated/models/ToolkitCredentialBindingResponse';
export type { ToolkitCredentialListResponse } from '@oss-internal/shared/api/generated/models/ToolkitCredentialListResponse';
export type { ToolkitCredentialBindRequest } from '@oss-internal/shared/api/generated/models/ToolkitCredentialBindRequest';
export type { ToolkitAgentResponse } from '@oss-internal/shared/api/generated/models/ToolkitAgentResponse';
export type { ToolkitAgentListResponse } from '@oss-internal/shared/api/generated/models/ToolkitAgentListResponse';
export type { PermissionRuleReadSchema } from '@oss-internal/shared/api/generated/models/PermissionRuleReadSchema';
// The codegen retag split the single `PermissionRuleSchema` model into two
// tag-namespaced variants (access-requests vs toolkits). Toolkit permission
// rules use the toolkit variant; re-export it under the stable public name so
// downstream consumers stay unchanged.
export type { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema as PermissionRuleSchema } from '@oss-internal/shared/api/generated/models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
export { jentic_one__control__web__schemas__toolkits__PermissionRuleSchema as PermissionRuleSchemaNS } from '@oss-internal/shared/api/generated/models/jentic_one__control__web__schemas__toolkits__PermissionRuleSchema';
export type { PermissionRuleListResponse } from '@oss-internal/shared/api/generated/models/PermissionRuleListResponse';
export type { PermissionsPatchRequest } from '@oss-internal/shared/api/generated/models/PermissionsPatchRequest';
export type { ToolkitBindingResponse } from '@oss-internal/shared/api/generated/models/ToolkitBindingResponse';
export type { ToolkitBindingListResponse } from '@oss-internal/shared/api/generated/models/ToolkitBindingListResponse';
export type { ToolkitBindRequest } from '@oss-internal/shared/api/generated/models/ToolkitBindRequest';
// Audit (read-only, toolkit-scoped lens on the shared /audit endpoint via AuditService).
export type { AuditResponse } from '@oss-internal/shared/api/generated/models/AuditResponse';
export type { AuditListResponse } from '@oss-internal/shared/api/generated/models/AuditListResponse';
export { AuditTargetType } from '@oss-internal/shared/api/generated/models/AuditTargetType';

// Generated models used by the auth/foundation layer.
export type { LoginRequest } from '@oss-internal/shared/api/generated/models/LoginRequest';
export type { LoginResponse } from '@oss-internal/shared/api/generated/models/LoginResponse';
export type { CurrentUserResponse } from '@oss-internal/shared/api/generated/models/CurrentUserResponse';
export type { ChangePasswordRequest } from '@oss-internal/shared/api/generated/models/ChangePasswordRequest';
export type { RedeemInviteRequest } from '@oss-internal/shared/api/generated/models/RedeemInviteRequest';
export type { HealthResponse } from '@oss-internal/shared/api/generated/models/HealthResponse';

// Agents / service-accounts / dynamic registration (ui-agents module).
// Note: AgentsService + ToolkitBinding* models are already exported above by
// the toolkits module; agents reuses them and does not re-export to avoid dupes.
export type { AgentCreateRequest } from '@oss-internal/shared/api/generated/models/AgentCreateRequest';
export type { AgentResponse } from '@oss-internal/shared/api/generated/models/AgentResponse';
export type { ApiKeyResponse } from '@oss-internal/shared/api/generated/models/ApiKeyResponse';
export type { ApiKeyInfoResponse } from '@oss-internal/shared/api/generated/models/ApiKeyInfoResponse';
export type { ApiKeyHistoryResponse } from '@oss-internal/shared/api/generated/models/ApiKeyHistoryResponse';
export type { ApiKeyHistoryEntryResponse } from '@oss-internal/shared/api/generated/models/ApiKeyHistoryEntryResponse';
export type { AgentListResponse } from '@oss-internal/shared/api/generated/models/AgentListResponse';
export type { ServiceAccountResponse } from '@oss-internal/shared/api/generated/models/ServiceAccountResponse';
export type { ServiceAccountListResponse } from '@oss-internal/shared/api/generated/models/ServiceAccountListResponse';
export type { ServiceAccountCreateRequest } from '@oss-internal/shared/api/generated/models/ServiceAccountCreateRequest';
export type { RegisterRequest } from '@oss-internal/shared/api/generated/models/RegisterRequest';
export type { RegisterResponse } from '@oss-internal/shared/api/generated/models/RegisterResponse';
export type { jentic_one__auth__web__schemas__agents__DenyRequest as AgentDenyRequest } from '@oss-internal/shared/api/generated/models/jentic_one__auth__web__schemas__agents__DenyRequest';
export type { jentic_one__auth__web__schemas__service_accounts__DenyRequest as ServiceAccountDenyRequest } from '@oss-internal/shared/api/generated/models/jentic_one__auth__web__schemas__service_accounts__DenyRequest';

// Agent Rail — the persistent live-event rail consumes the REAL platform event
// feed (`/events` + `/events/stream` SSE) via `EventsService` (formerly the
// coarse `AdminService`). These models are already generated; re-exported here
// (append-only) so the rail's data layer in `shared/lib/agentStream` can stay
// behind the facade like every other module.
export type { EventResponse } from '@oss-internal/shared/api/generated/models/EventResponse';
export type { EventListResponse } from '@oss-internal/shared/api/generated/models/EventListResponse';
export type { EventLinks } from '@oss-internal/shared/api/generated/models/EventLinks';
export type { EventAcknowledgeRequest } from '@oss-internal/shared/api/generated/models/EventAcknowledgeRequest';
export { EventSeverity } from '@oss-internal/shared/api/generated/models/EventSeverity';

// Discover (catalog) slice — services + models. The former `ApisService` was
// renamed `ApIsService` by the retag (+ ApiSpecService / ApiOperationsService).
export { CatalogService } from '@oss-internal/shared/api/generated/services/CatalogService';
export { ApIsService } from '@oss-internal/shared/api/generated/services/ApIsService';
export { ApiSpecService } from '@oss-internal/shared/api/generated/services/ApiSpecService';
export { ApiOperationsService } from '@oss-internal/shared/api/generated/services/ApiOperationsService';
export type { CatalogListResponse } from '@oss-internal/shared/api/generated/models/CatalogListResponse';
export type { CatalogEntryResponse } from '@oss-internal/shared/api/generated/models/CatalogEntryResponse';
export type { CatalogEntryLinksResponse } from '@oss-internal/shared/api/generated/models/CatalogEntryLinksResponse';
export type { CatalogRefreshResponse } from '@oss-internal/shared/api/generated/models/CatalogRefreshResponse';
export type { OperationPreviewListResponse } from '@oss-internal/shared/api/generated/models/OperationPreviewListResponse';
export type { PreviewOperationResponse } from '@oss-internal/shared/api/generated/models/PreviewOperationResponse';
export type { PreviewInfoResponse } from '@oss-internal/shared/api/generated/models/PreviewInfoResponse';
export type { PreviewParameterResponse } from '@oss-internal/shared/api/generated/models/PreviewParameterResponse';
export type { ApiImportResponse } from '@oss-internal/shared/api/generated/models/ApiImportResponse';
export type { ApiListResponse } from '@oss-internal/shared/api/generated/models/ApiListResponse';
export type { ApiResponse } from '@oss-internal/shared/api/generated/models/ApiResponse';
export type { ApiReferenceResponse } from '@oss-internal/shared/api/generated/models/ApiReferenceResponse';
export type { ApiLinksResponse } from '@oss-internal/shared/api/generated/models/ApiLinksResponse';

// [ui-dashboard] Execution models the overview renders. The Event* models
// (EventResponse/EventListResponse/EventSeverity) the Dashboard also needs are
// already exported above by the Agent Rail block; re-exporting them here would
// be a duplicate, so Dashboard reuses those and only adds the Execution models.
export type { ExecutionResponse } from '@oss-internal/shared/api/generated/models/ExecutionResponse';
export type { ExecutionListResponse } from '@oss-internal/shared/api/generated/models/ExecutionListResponse';

// Credentials module (feat/ui-credentials).
export { CredentialsService } from '@oss-internal/shared/api/generated/services/CredentialsService';
export { CredentialType } from '@oss-internal/shared/api/generated/models/CredentialType';
export { CredentialLocation } from '@oss-internal/shared/api/generated/models/CredentialLocation';
export type { ApiKeyCreateRequest } from '@oss-internal/shared/api/generated/models/ApiKeyCreateRequest';
export type { ApiKeyUpdateRequest } from '@oss-internal/shared/api/generated/models/ApiKeyUpdateRequest';
export type { BasicAuthCreateRequest } from '@oss-internal/shared/api/generated/models/BasicAuthCreateRequest';
export type { BasicAuthUpdateRequest } from '@oss-internal/shared/api/generated/models/BasicAuthUpdateRequest';
export type { BearerTokenCreateRequest } from '@oss-internal/shared/api/generated/models/BearerTokenCreateRequest';
export type { BearerTokenUpdateRequest } from '@oss-internal/shared/api/generated/models/BearerTokenUpdateRequest';
export type { OAuth2CreateRequest } from '@oss-internal/shared/api/generated/models/OAuth2CreateRequest';
export type { OAuth2UpdateRequest } from '@oss-internal/shared/api/generated/models/OAuth2UpdateRequest';
export type { APIReference } from '@oss-internal/shared/api/generated/models/APIReference';
export type { APIReferenceRequest } from '@oss-internal/shared/api/generated/models/APIReferenceRequest';
export type { RuntimeConfig } from '@oss-internal/shared/api/generated/models/RuntimeConfig';
export type { ConnectRequestBody } from '@oss-internal/shared/api/generated/models/ConnectRequestBody';
export type { ConnectChallengeResponse } from '@oss-internal/shared/api/generated/models/ConnectChallengeResponse';
export type { CredentialCreateResponse } from '@oss-internal/shared/api/generated/models/CredentialCreateResponse';
export type { CredentialListResponse } from '@oss-internal/shared/api/generated/models/CredentialListResponse';
export type { CredentialRedactedResponse } from '@oss-internal/shared/api/generated/models/CredentialRedactedResponse';
export type { ProviderDiscoveryResponse } from '@oss-internal/shared/api/generated/models/ProviderDiscoveryResponse';
export type { ProviderDiscoveryEntryResponse } from '@oss-internal/shared/api/generated/models/ProviderDiscoveryEntryResponse';

// Agent Rail — access-request decisions (`POST /access-requests/{id}:decide`).
// The access-request router (tag "Access Requests") is now exposed as a
// generated `AccessRequestsService` after the codegen retag. The rail's
// access-request repository (`shared/lib/accessRequests`) still issues its calls
// through the low-level request primitive the generated services use, kept
// behind the facade so the Bearer-JWT `OpenAPI` config still applies; switching
// it to `AccessRequestsService` is a safe follow-up. Append-only, like the rest.
export { AccessRequestsService } from '@oss-internal/shared/api/generated/services/AccessRequestsService';
export { OpenAPI } from '@oss-internal/shared/api/generated/core/OpenAPI';
export { request as apiRequest } from '@oss-internal/shared/api/generated/core/request';

// First-run setup (feat/no-credential-first-run). The one-time create-admin
// endpoint bootstraps the first operator account; CreateAdminRequest is its body.
export type { CreateAdminRequest } from '@oss-internal/shared/api/generated/models/CreateAdminRequest';
// Shared react-query key for the first-run health/setup probe (SetupGate reads
// it; the create-admin flow invalidates it). Kept here so reader and invalidator
// import the same constant and can't drift.
export { HEALTH_QUERY_KEY } from '@oss-internal/shared/api/health';

// Cross-module query-key registry (#511). Owns the few cache-key roots that
// cross a module boundary so each is defined once; a module invalidates a
// sibling's cache through this instead of a hand-synced raw key literal.
export { sharedQueryKeys } from '@oss-internal/shared/api/queryKeys';

// Actor scopes (#615). The platform permission catalogue + the agent/service-
// account scope grant endpoints. `AgentsService`/`ServiceAccountsService` are
// already exported above (toolkits/agents blocks); these add the permission
// catalogue service and the scope request/response models the agents module
// wires into the Scopes card. Append-only.
export { PermissionsService } from '@oss-internal/shared/api/generated/services/PermissionsService';
export type { PermissionResponse } from '@oss-internal/shared/api/generated/models/PermissionResponse';
export type { PermissionListResponse } from '@oss-internal/shared/api/generated/models/PermissionListResponse';
export type { AgentScopesRequest } from '@oss-internal/shared/api/generated/models/AgentScopesRequest';
export type { AgentScopesResponse } from '@oss-internal/shared/api/generated/models/AgentScopesResponse';
export type { ServiceAccountScopesRequest } from '@oss-internal/shared/api/generated/models/ServiceAccountScopesRequest';
export type { ServiceAccountScopesResponse } from '@oss-internal/shared/api/generated/models/ServiceAccountScopesResponse';

// --- Monitor module (executions / jobs / events / audit) -------------------
// Re-exported through the facade so the Monitor repository tier consumes typed
// services + models from `@/shared/api` rather than reaching into `./generated`
// (ESLint layering forbids the latter). The retag split the old coarse
// AdminService into per-tag services: Monitor's tabs use ExecutionsService /
// JobsService / EventsService / AuditService — all exported above.
// Note: Execution*, Event*, and Audit* models are already exported above (by the
// dashboard, agent-rail, and toolkits blocks respectively); Monitor reuses them.
// Only the Job models are not yet re-exported, so add them here (append-only).
export type { JobResponse } from '@oss-internal/shared/api/generated/models/JobResponse';
export type { JobListResponse } from '@oss-internal/shared/api/generated/models/JobListResponse';

// Monitor Overview: the aggregation endpoint (GET /monitoring/executions,
// `MonitoringService.getExecutionStats`) added by jentic-one#386. Powers the
// Overview usage charts + top-operations panel.
export { MonitoringService } from '@oss-internal/shared/api/generated/services/MonitoringService';
export type { ExecutionStatsResponse } from '@oss-internal/shared/api/generated/models/ExecutionStatsResponse';
export type { DailyExecutionBucket } from '@oss-internal/shared/api/generated/models/DailyExecutionBucket';
export type { TopOperation } from '@oss-internal/shared/api/generated/models/TopOperation';

// Monitor global filter bar: the actor directory (GET /actors,
// `ActorsService.listActors`) hydrates the actor picker shared across the
// Executions/Events/Audit tabs. Also consumed by the shared actor-directory
// hook (`useActorDirectory`) + `<ActorLabel>` to resolve raw `actor_id` values
// into human-readable names across access-request and agent surfaces.
// `ActorType` is exported as a *value* (not just a type) because `<ActorLabel>`
// reads the enum members for its subtle type prefix. Append-only.
export { ActorsService } from '@oss-internal/shared/api/generated/services/ActorsService';
export { ActorType } from '@oss-internal/shared/api/generated/models/ActorType';
export type { ActorListResponse } from '@oss-internal/shared/api/generated/models/ActorListResponse';
export type { ActorSummaryResponse } from '@oss-internal/shared/api/generated/models/ActorSummaryResponse';
