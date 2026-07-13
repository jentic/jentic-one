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
