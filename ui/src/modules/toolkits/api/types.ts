/**
 * Toolkits module — UI-facing types (the "schemas" tier, ≈ backend web+service
 * schemas). These mirror the real `control` toolkit contract verified against
 * the live `/openapi.json` (`ToolkitResponse`, key/credential/permission
 * shapes). Views and hooks consume these; only `api/client.ts` touches the
 * generated models behind the `@/shared/api` facade.
 */
import type {
	ToolkitResponse,
	ToolkitListResponse,
	ToolkitKeyResponse,
	ToolkitKeyListResponse,
	ToolkitCredentialBindingResponse,
	ToolkitCredentialListResponse,
	PermissionRuleReadSchema,
	ToolkitBindingResponse,
	AuditResponse,
	UsageResponse,
	UsageBucket,
	BindingWarningSchema,
	PermissionTestResponse,
} from '@/shared/api';

/** A toolkit as rendered by the list/detail UI. */
export type Toolkit = ToolkitResponse;

/** Cursor-paginated toolkit list envelope. */
export type ToolkitList = ToolkitListResponse;

/** A toolkit API key (plaintext is only present on the create response). */
export type ToolkitKey = ToolkitKeyResponse;
export type ToolkitKeyList = ToolkitKeyListResponse;

/** A credential bound to a toolkit. */
export type ToolkitCredentialBinding = ToolkitCredentialBindingResponse;
export type ToolkitCredentialList = ToolkitCredentialListResponse;

/** A permission rule on a toolkit↔credential binding. */
export type PermissionRule = PermissionRuleReadSchema;

/** Write shape for a permission rule (allow/deny + methods/path/operations). */
export type { PermissionRuleInput } from '@/shared/ui';

/**
 * Rule effect values, as plain string literals matching the backend enum
 * (`allow` / `deny`). Re-exported from the shared `PermissionRuleEditor` home so
 * the editor and the toolkits module share one definition.
 */
export { PERMISSION_EFFECTS } from '@/shared/ui';
export type { PermissionEffect } from '@/shared/ui';

/** An agent binding (from `GET /agents/{id}/toolkits` — agent side). */
export type AgentToolkitBinding = ToolkitBindingResponse;

/** A single audit-log entry (read-only, from the shared `/audit` endpoint). */
export type ToolkitAuditEntry = AuditResponse;

/** Result of creating a toolkit: the toolkit plus its one-time plaintext key. */
export interface CreatedToolkit {
	toolkit: Toolkit;
	/** Plaintext `jntc_live_…` key — shown once, never retrievable again. */
	apiKey: string;
}

/**
 * Credential auth-type values as plain string literals matching the backend
 * enum (`CredentialType`). Declared here so view/picker code can label and
 * filter without importing the generated enum *value* (the layering ESLint
 * rule forbids that outside `api/client.ts`).
 */
export const CREDENTIAL_TYPES = ['api_key', 'bearer_token', 'basic', 'oauth2'] as const;
export type BindableCredentialType = (typeof CREDENTIAL_TYPES)[number];

/** Short, user-facing labels for each credential auth type. */
export const CREDENTIAL_TYPE_LABELS: Record<string, string> = {
	api_key: 'API key',
	bearer_token: 'Bearer',
	basic: 'Basic',
	oauth2: 'OAuth 2.0',
};

/**
 * Minimal projection of a workspace credential for the toolkit bind picker —
 * just what the picker needs to render and filter a row. Sourced from the
 * org-wide `GET /credentials` surface via the repository tier.
 */
export interface BindableCredential {
	credential_id: string;
	name: string;
	type: string;
	vendor: string | null;
	/** Null/empty ⇒ the credential is vendor-wide (covers every sub-API) (#744). */
	api_name: string | null;
	provider: string | null;
}

/**
 * Minimal projection of an agent for the toolkit detail page's "Bound Agents"
 * section. Used both for agents bound to the toolkit (`bound_at` set, from the
 * reverse `GET /toolkits/{id}/agents`) and for the link picker's candidate list
 * (`bound_at` null, from `GET /agents`).
 */
export interface ToolkitAgent {
	agent_id: string;
	agent_name: string;
	status: string;
	bound_at: string | null;
}

/**
 * Per-toolkit usage aggregation (`GET /monitoring/usage?toolkit_id=…`) — the
 * detail page's KPI strip and Activity chart. Admin-gated: the repository maps
 * 401/403 to `null` so the surfaces hide rather than error for non-admins.
 */
export type ToolkitUsage = UsageResponse;

/** One time bucket of the usage aggregation (unix-second `ts`). */
export type ToolkitUsageBucket = UsageBucket;

/**
 * Minimal projection of an execution record for the toolkit Activity feed —
 * just what a feed row renders. Sourced from the admin `GET /executions`
 * surface via the repository tier (401/403 → `null`, same gating as usage).
 */
export interface ToolkitExecution {
	execution_id: string;
	trace_id: string;
	status: string;
	operation_id: string | null;
	api_label: string | null;
	actor_id: string;
	actor_type: string;
	http_status: number | null;
	duration_ms: number | null;
	error: string | null;
	started_at: string;
}

/**
 * Non-fatal bind-time signal on a credential binding (e.g. "zero rules — the
 * broker denies by default"). Returned by both the bind call and the bindings
 * list; rendered verbatim on the Access tab.
 */
export type BindingWarning = BindingWarningSchema;

/**
 * Broker dry-run verdict from `POST …/permissions:test`. Vendor pooling means
 * `credential_id` may name a *different* binding than the one tested against.
 */
export type PermissionTestResult = PermissionTestResponse;

/**
 * One toolkit's slice of the `group_by=toolkit` usage aggregation — the list
 * page's card sparklines (7d volume trend + totals).
 */
export interface ToolkitUsageSummary {
	total: number;
	success: number;
	failed: number;
	trend: number[];
}
