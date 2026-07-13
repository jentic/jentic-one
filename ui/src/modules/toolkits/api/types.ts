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
	PermissionRuleSchema,
	ToolkitBindingResponse,
	AuditResponse,
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
export type PermissionRuleInput = PermissionRuleSchema;

/**
 * Rule effect values, as plain string literals matching the backend enum
 * (`allow` / `deny`). Defined here so views/editors don't import the generated
 * enum *value* (which the layering ESLint rule forbids outside `api/client.ts`).
 */
export const PERMISSION_EFFECTS = ['allow', 'deny'] as const;
export type PermissionEffect = (typeof PERMISSION_EFFECTS)[number];

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
