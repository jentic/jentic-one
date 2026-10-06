// API facade — feature modules import from `@/shared/api`, never from
// `./generated` directly, so the Bearer-JWT client config is always applied.
// Add re-exports by appending a block (so parallel feature PRs don't collide);
// remove a re-export once nothing outside the facade imports it.

// Client config + auth-error helper (side-effect import configures generated client).
export { ApiError, isAuthError, isClientError } from '@/shared/api/client';

// Bearer-JWT token store.
export { getToken, setToken, clearToken, subscribeToken } from '@/shared/api/token-store';

// Health (deploy-mode aware).
export { getHealth } from '@/shared/api/health';

// Generated typed services (regenerate with `npm run codegen`), one per API tag.
export { UsersService } from '@/shared/api/generated/services/UsersService';
export { EventsService } from '@/shared/api/generated/services/EventsService';
export { ExecutionsService } from '@/shared/api/generated/services/ExecutionsService';
export { AuditService } from '@/shared/api/generated/services/AuditService';
export { JobsService } from '@/shared/api/generated/services/JobsService';
export { SystemService } from '@/shared/api/generated/services/SystemService';

// Agents. Agent-side credential bindings live on `AgentsService` (the /agents
// router).
export { AgentsService } from '@/shared/api/generated/services/AgentsService';
export type { PermissionRuleReadSchema } from '@/shared/api/generated/models/PermissionRuleReadSchema';
export type { PermissionRuleSchema } from '@/shared/api/generated/models/PermissionRuleSchema';
export type { PermissionTestRequest } from '@/shared/api/generated/models/PermissionTestRequest';
export type { PermissionTestResponse } from '@/shared/api/generated/models/PermissionTestResponse';
// Audit (read-only lens on the shared /audit endpoint via AuditService).
export type { AuditResponse } from '@/shared/api/generated/models/AuditResponse';
export type { AuditListResponse } from '@/shared/api/generated/models/AuditListResponse';
export { AuditTargetType } from '@/shared/api/generated/models/AuditTargetType';

// Generated models used by the auth/foundation layer.
export type { LoginRequest } from '@/shared/api/generated/models/LoginRequest';
export type { LoginResponse } from '@/shared/api/generated/models/LoginResponse';
export type { CurrentUserResponse } from '@/shared/api/generated/models/CurrentUserResponse';

// Agent models (`AgentsService` is exported above).
export type { AgentResponse } from '@/shared/api/generated/models/AgentResponse';
export type { AgentListResponse } from '@/shared/api/generated/models/AgentListResponse';

// Agent Rail — the persistent live-event rail consumes the REAL platform event
// feed (`/events` + `/events/stream` SSE) via `EventsService`. These models
// are already generated; re-exported here
// (append-only) so the rail's data layer in `shared/lib/agentStream` can stay
// behind the facade like every other module.
export type { EventResponse } from '@/shared/api/generated/models/EventResponse';
export type { EventListResponse } from '@/shared/api/generated/models/EventListResponse';
export type { EventAcknowledgeRequest } from '@/shared/api/generated/models/EventAcknowledgeRequest';
export { EventSeverity } from '@/shared/api/generated/models/EventSeverity';

// Discover (catalog) slice — services + models.
export { CatalogService } from '@/shared/api/generated/services/CatalogService';
export { ApIsService } from '@/shared/api/generated/services/ApIsService';
export { ApiSpecService } from '@/shared/api/generated/services/ApiSpecService';
export { ApiOperationsService } from '@/shared/api/generated/services/ApiOperationsService';
export type { CatalogListResponse } from '@/shared/api/generated/models/CatalogListResponse';
export type { CatalogEntryResponse } from '@/shared/api/generated/models/CatalogEntryResponse';
export type { CatalogRefreshResponse } from '@/shared/api/generated/models/CatalogRefreshResponse';
export type { OperationPreviewListResponse } from '@/shared/api/generated/models/OperationPreviewListResponse';
export type { PreviewOperationResponse } from '@/shared/api/generated/models/PreviewOperationResponse';
export type { ApiImportResponse } from '@/shared/api/generated/models/ApiImportResponse';
export type { ApiListResponse } from '@/shared/api/generated/models/ApiListResponse';
export type { ApiResponse } from '@/shared/api/generated/models/ApiResponse';

// Execution models (Monitor's calls view, agent activity).
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
export type { NoAuthCreateRequest } from '@/shared/api/generated/models/NoAuthCreateRequest';
export type { OAuth2CreateRequest } from '@/shared/api/generated/models/OAuth2CreateRequest';
export type { OAuth2UpdateRequest } from '@/shared/api/generated/models/OAuth2UpdateRequest';
export type { Sigv4CreateRequest } from '@/shared/api/generated/models/Sigv4CreateRequest';
export type { Sigv4UpdateRequest } from '@/shared/api/generated/models/Sigv4UpdateRequest';
export type { APIReference } from '@/shared/api/generated/models/APIReference';
export type { APIReferenceRequest } from '@/shared/api/generated/models/APIReferenceRequest';
export type { RuntimeConfig } from '@/shared/api/generated/models/RuntimeConfig';
export type { ConnectRequestBody } from '@/shared/api/generated/models/ConnectRequestBody';
// The `POST /credentials/{id}/connect` response is a Pydantic discriminated
// union (authorization_code | device_code); the codegen collapses it to `any`.
// The hand-authored `ConnectChallengeResponse` in `shared/credentials/api/types.ts`
// is the source of truth for the wire shape — import from there.
export type { CredentialCreateResponse } from '@/shared/api/generated/models/CredentialCreateResponse';
export type { CredentialListResponse } from '@/shared/api/generated/models/CredentialListResponse';
export type { CredentialRedactedResponse } from '@/shared/api/generated/models/CredentialRedactedResponse';
export type { ProviderDiscoveryResponse } from '@/shared/api/generated/models/ProviderDiscoveryResponse';
export type { ProviderDiscoveryEntryResponse } from '@/shared/api/generated/models/ProviderDiscoveryEntryResponse';

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

// Actor scopes (#615). The platform permission catalogue + the agent scope
// grant endpoints (on `AgentsService`, exported above) the agents module wires
// into the Scopes card.
export { PermissionsService } from '@/shared/api/generated/services/PermissionsService';
export type { PermissionResponse } from '@/shared/api/generated/models/PermissionResponse';

// --- Monitor module (executions / jobs / events / audit) -------------------
// Re-exported through the facade so the Monitor repository tier consumes typed
// services + models from `@/shared/api` rather than reaching into `./generated`
// (ESLint layering forbids the latter). Execution, event and audit services
// and models are exported above; the Job models are added here.
export type { JobResponse } from '@/shared/api/generated/models/JobResponse';
export type { JobListResponse } from '@/shared/api/generated/models/JobListResponse';

// Monitor Overview parity: the enriched usage-aggregation endpoint
// (GET /monitoring/usage, `MonitoringService.getUsageStats`).
// Powers the Monitor Overview — bubble chart, per-row sparkline
// trends, latency pills, and the api/credential/agent grouping toggle. `GroupBy`
// is exported as a *value* because callers pass the enum members as the
// `group_by` query param.
export { MonitoringService } from '@/shared/api/generated/services/MonitoringService';
export { GroupBy } from '@/shared/api/generated/models/GroupBy';
export type { UsageResponse } from '@/shared/api/generated/models/UsageResponse';

// Monitor global filter bar: the actor directory (GET /actors,
// `ActorsService.listActors`) hydrates the actor picker shared across the
// Executions/Events/Audit tabs. Also consumed by the shared actor-directory
// hook (`useActorDirectory`) + `<ActorLabel>` to resolve raw `actor_id` values
// into human-readable names across monitor and agent surfaces.
// `ActorType` is exported as a *value* (not just a type) because `<ActorLabel>`
// reads the enum members for its subtle type prefix. Append-only.
export { ActorsService } from '@/shared/api/generated/services/ActorsService';
export { ActorType } from '@/shared/api/generated/models/ActorType';
export type { ActorListResponse } from '@/shared/api/generated/models/ActorListResponse';
export type { ActorSummaryResponse } from '@/shared/api/generated/models/ActorSummaryResponse';
// By-id name lookup (`GET /actors/lookup`, `ActorsService.lookupActors`): the
// directory's fallback for callers without `users:read`.
export type { ActorLookupEntryResponse } from '@/shared/api/generated/models/ActorLookupEntryResponse';
export type { ActorLookupResponse } from '@/shared/api/generated/models/ActorLookupResponse';

// Session lifecycle (#610/#608): expiry-aware token adoption + proactive
// refresh scheduling + the one-shot "session expired" login notice.
export {
	setSession,
	getSessionExpiresAt,
	consumeSessionExpiredNotice,
} from '@/shared/api/token-store';

// Overlays (#937). The overlay lifecycle
// service backing the workspace OverlaysSection + the "close the overlay-update
// loop" flow (list/get/confirm/rollback/deprecate). List/get responses are
// typed `any` on the generated client; the workspace module re-types them in
// its own `api/types.ts` + `adapters.ts`. Append-only, like the rest.
export { OverlaysService } from '@/shared/api/generated/services/OverlaysService';

// Notes (`GET /notes`, any authenticated caller). Agent/operator-authored hints
// attached to an API (auth quirks, usage hints, corrections) — read by the
// Library's API hub Overview. List responses are typed `any` on the generated
// client; the workspace module re-types them in its adapters. Append-only.
export { NotesService } from '@/shared/api/generated/services/NotesService';

// System version. The running vs. latest-
// available app release, read by the shell's update banner + UserMenu version
// line via `useVersionInfo`. `SystemService` is already exported above; only the
// response model is added here. Append-only.
export type { VersionResponse } from '@/shared/api/generated/models/VersionResponse';

// OAuth client management (third-party application registrations). Append-only.
export { OAuthClientsService } from '@/shared/api/generated/services/OAuthClientsService';
// Value export (not type-only): the create request carries enum namespaces
// (`consent_model`, `token_endpoint_auth_method`) the form sheet needs.
export { OAuthClientCreateRequest } from '@/shared/api/generated/models/OAuthClientCreateRequest';
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

// OAuth consent grants. The per-agent
// "Connected clients" listing lives on `AgentsService.listAgentOauthGrants`
// (already exported above); the grant kill switch (`POST
// /oauth-grants/{id}:revoke`) and the admin cross-view (`GET
// /admin/oauth-grants`) live on `OAuthService`. Append-only, like the rest.
export { OAuthService } from '@/shared/api/generated/services/OAuthService';
export type { OAuthGrantResponse } from '@/shared/api/generated/models/OAuthGrantResponse';
// Admin cross-view rows (`GET /admin/oauth-grants`) — the per-client grants
// panel in the OAuth-clients detail sheet. Append-only, like the rest.
export type { OAuthGrantAdminResponse } from '@/shared/api/generated/models/OAuthGrantAdminResponse';
export type { OAuthGrantAdminListResponse } from '@/shared/api/generated/models/OAuthGrantAdminListResponse';

// Direct agent↔credential bindings (theme 5 phase 1). The agent-side surface
// (list/bind/unbind/resume) lives on `AgentsService`; the credential-side
// reverse lookup and per-binding rule list / replace / dry-run live on
// `CredentialsService` (both services already exported above) — only the
// request/response models are added here. Append-only, like the rest.
export type { CredentialBindingResponse } from '@/shared/api/generated/models/CredentialBindingResponse';
export type { CredentialAgentResponse } from '@/shared/api/generated/models/CredentialAgentResponse';
export type { CredentialAgentListResponse } from '@/shared/api/generated/models/CredentialAgentListResponse';

// RFC 9457 problem bodies: the string `detail` callers surface over the
// transport's status text. Append-only, like the rest.
export { problemDetailText } from '@/shared/api/problem';
