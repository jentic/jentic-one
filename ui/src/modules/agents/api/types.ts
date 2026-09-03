/**
 * Agents module — UI-facing types & adapters.
 *
 * The domain vocabulary is the backend's `Actor*` enums (formalized on `main`,
 * `shared/models/actors.py`): the status/verb unions below mirror those values
 * verbatim. The web response schema still serializes attribution as
 * `registered_by`/`approved_by`/`denied_by` (NOT yet `actor_id`/`actor_type`),
 * so we adapt the served `AgentResponse`/`ServiceAccountResponse` into neutral
 * entity envelopes here. When the web schema is regenerated to
 * `actor_id`/`actor_type`, only these adapters change — hooks/views are
 * unaffected.
 */
import type { AgentResponse, ServiceAccountResponse } from '@/shared/api';
import {
	ACTOR_STATUSES,
	STATUS_BADGE_VARIANT,
	STATUS_DOT,
	STATUS_LABELS,
	toActorStatus,
	type ActorStatus,
} from '@/shared/ui';

// The actor status vocabulary (union + label/variant/dot maps + `toActorStatus`)
// now lives in `shared/ui` so every module renders an actor status identically
// (module-boundary rule: siblings can't import each other). Re-exported here so
// the agents module's public API (`@/modules/agents/api`) stays stable.
export { ACTOR_STATUSES, STATUS_BADGE_VARIANT, STATUS_DOT, STATUS_LABELS, toActorStatus };
export type { ActorStatus };

/** Mirrors `ActorVerb` (approve|deny|disable|enable). Archive is a DELETE, not a verb. */
export type ActorVerb = 'approve' | 'deny' | 'disable' | 'enable';

/**
 * Allowed inline lifecycle actions per status — the single source of truth the
 * roster/detail use to decide which buttons to render. Matches the backend state
 * machine (`agent_service.py`): pending→approve/deny, active→disable,
 * disabled→enable. `archive` is allowed from any non-archived status (the
 * backend only rejects archiving an already-archived actor), so pending and
 * rejected actors can be cleaned up too. `archived` is terminal.
 */
export type AgentAction = ActorVerb | 'archive';

export const ACTIONS_FOR_STATUS: Record<ActorStatus, AgentAction[]> = {
	pending: ['approve', 'deny', 'archive'],
	active: ['disable', 'archive'],
	disabled: ['enable', 'archive'],
	rejected: ['archive'],
	archived: [],
};

/** Human label per lifecycle action — shared across roster + detail surfaces. */
export const ACTION_LABEL: Record<AgentAction, string> = {
	approve: 'Approve',
	deny: 'Deny',
	disable: 'Disable',
	enable: 'Enable',
	archive: 'Archive',
};

/**
 * Button variant per lifecycle action — one source of truth so the destructive
 * emphasis is identical on the roster and the detail page.
 */
export const ACTION_VARIANT: Record<AgentAction, 'primary' | 'secondary' | 'danger' | 'outline'> = {
	approve: 'primary',
	enable: 'primary',
	deny: 'danger',
	disable: 'danger',
	archive: 'secondary',
};

/** Neutral attribution shape — insulates views from the served field names. */
export interface Attribution {
	registeredBy: string | null;
	approvedBy: string | null;
	deniedBy: string | null;
}

/** UI envelope for an agent. */
export interface AgentEntity {
	id: string;
	name: string;
	description: string | null;
	status: ActorStatus;
	ownerId: string | null;
	parentAgentId: string | null;
	denialReason: string | null;
	createdAt: string;
	approvedAt: string | null;
	attribution: Attribution;
	hasApiKey: boolean;
}

/** UI envelope for a service account. */
export interface ServiceAccountEntity {
	id: string;
	name: string;
	description: string | null;
	status: ActorStatus;
	ownerId: string;
	denialReason: string | null;
	createdAt: string;
	approvedAt: string | null;
	attribution: Attribution;
}

export function agentToEntity(r: AgentResponse): AgentEntity {
	return {
		id: r.id,
		name: r.name,
		description: r.description ?? null,
		status: toActorStatus(r.status),
		ownerId: r.owner_id ?? null,
		parentAgentId: r.parent_agent_id ?? null,
		denialReason: r.denial_reason ?? null,
		createdAt: r.created_at,
		approvedAt: r.approved_at ?? null,
		attribution: {
			registeredBy: r.registered_by ?? null,
			approvedBy: r.approved_by ?? null,
			deniedBy: r.denied_by ?? null,
		},
		hasApiKey: r.has_api_key ?? false,
	};
}

export function serviceAccountToEntity(r: ServiceAccountResponse): ServiceAccountEntity {
	return {
		id: r.id,
		name: r.name,
		description: r.description ?? null,
		status: toActorStatus(r.status),
		ownerId: r.owner_id,
		denialReason: r.denial_reason ?? null,
		createdAt: r.created_at,
		approvedAt: r.approved_at ?? null,
		attribution: {
			registeredBy: r.registered_by ?? null,
			approvedBy: r.approved_by ?? null,
			deniedBy: r.denied_by ?? null,
		},
	};
}

/** A bound toolkit (read-only list in the detail sheet). */
export interface ToolkitBindingEntity {
	id: string;
	toolkitId: string;
	boundAt: string;
}

/**
 * A candidate toolkit for the agent-side "Bind toolkit" picker (#607). A small
 * projection of the shared `ToolkitResponse` — the agents module keeps its own
 * picker (module-boundary rule forbids importing the toolkits module), so it
 * only needs id/name/active to render and filter the list.
 */
export interface LinkableToolkit {
	toolkitId: string;
	name: string;
	active: boolean;
}

/** Result of generating an API key — the plaintext shown once. */
export interface ApiKeyResult {
	key: string;
}

/** API key metadata — retrievable even after revocation. */
export interface ApiKeyInfoEntity {
	id: string;
	status: 'active' | 'revoked';
	createdAt: string;
	rotatedAt: string | null;
	createdBy: string | null;
}

/** A single event in the API key audit trail. */
export interface ApiKeyHistoryEntry {
	id: string;
	action: string;
	reason: string | null;
	actorId: string | null;
	occurredAt: string;
}

/**
 * A platform permission from the catalogue (`GET /permissions`). These are the
 * scope vocabulary that actor `scopes` draw from — distinct from the OAuth2
 * provider scopes the credentials picker uses. `grantableByCaller` is false for
 * permissions the current operator lacks the authority to grant.
 */
export interface PermissionCatalogEntry {
	name: string;
	description: string;
	implies: string[];
	grantableByCaller: boolean;
}

// ---------------------------------------------------------------------------
// MCP transport visibility (local-MCP 2-E2, #1188).
//
// MCP is a TRANSPORT of an existing agent, not a new entity — nothing new in
// the data model. These shapes are read straight off existing surfaces: the
// `mcp.session_started` internal event's `data` (`GET /events`) and the
// MCP-origin execution records (`GET /executions?origin=mcp`).
// ---------------------------------------------------------------------------

/**
 * One MCP session recorded for an agent — a projection of the
 * `mcp.session_started` internal event. `transport` is what the emitter knew
 * (`stdio` today; `http` when phase 3's mounted app lands) and renders
 * verbatim so a future value degrades gracefully. `clientName`/`clientVersion`
 * come from the relayed MCP clientInfo and are null when the client didn't
 * send it (a SHOULD in the MCP spec) — "client unknown", not an error.
 */
export interface McpSessionEntity {
	eventId: string;
	sessionId: string | null;
	transport: string | null;
	clientName: string | null;
	clientVersion: string | null;
	startedAt: string;
}

/** The latest MCP session per agent — the roster's "last seen via MCP" cell. */
export interface McpLastSeen {
	clientName: string | null;
	clientVersion: string | null;
	startedAt: string;
}

/** "claude-desktop 1.5.2" (or "unknown client") — one label rule everywhere. */
export function mcpClientLabel(s: {
	clientName: string | null;
	clientVersion: string | null;
}): string {
	if (!s.clientName) return 'unknown client';
	return s.clientVersion ? `${s.clientName} ${s.clientVersion}` : s.clientName;
}

/**
 * The backend's self-described identity (`GET /instance`) — which install a
 * pasted MCP snippet will talk to. `baseUrl`/`host` are '' when the operator
 * never configured a canonical base URL; callers fall back to the browser's
 * origin (the URL the operator is looking at IS an address of this instance).
 */
export interface InstanceIdentityEntity {
	/** 'local' | 'remote' — operator-declared locality hint. */
	backend: string;
	baseUrl: string;
	host: string;
	/**
	 * Whether the instance serves the daemon-native Streamable HTTP `/mcp`
	 * endpoint (`server.mcp.enabled`, phase 3) — gates the config card's HTTP
	 * variant so the UI never advertises a transport that 404s.
	 */
	mcpEnabled: boolean;
}

// ---------------------------------------------------------------------------
// OAuth consent grants (phase-3a §4.8) — the detail console's "Connected
// clients" panel: which OAuth clients hold a live consent→agent grant.
// ---------------------------------------------------------------------------

/**
 * One consent→agent grant (`GET /agents/{id}/oauth-grants`). `userId` is the
 * CONSENTING user, surfaced deliberately: after an agent ownership transfer
 * the grant stays with the original consenter (gap G10), so the panel must
 * show who holds it, not assume the current owner does. `canRevoke` is the
 * server-computed revoke capability for the CALLER — the revoke predicate
 * (consenting user or write-set admin) deliberately diverges from the list
 * predicate (agent's current owner or read-set admin), so a viewer may see a
 * grant they cannot revoke; the card disables the button instead of offering
 * an action that would 403.
 */
export interface OAuthGrantEntity {
	id: string;
	oauthClientId: string;
	clientName: string | null;
	clientOrigin: string | null;
	userId: string;
	agentId: string;
	scopes: string[];
	status: string;
	createdAt: string;
	revokedAt: string | null;
	lastUsedAt: string | null;
	canRevoke: boolean;
}
