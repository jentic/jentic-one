/**
 * Agents repository tier.
 *
 * The ONLY place in the Agents module that talks to `@/shared/api` (the HTTP
 * facade). Views and hooks never import the facade directly — ESLint enforces
 * this. Mirrors the backend Repository layer: thin wrappers that turn typed
 * service calls into UI entities and normalize errors into a single sentinel.
 *
 * Response-code contract (verified against the real backend on :8000):
 *   :approve / :deny           → 200 + AgentResponse  (return the updated row)
 *   :disable / :enable / DELETE → 204 no body          (callers refetch)
 */
import {
	ApiError,
	AgentsService,
	AuditService,
	AuditTargetType,
	EventsService,
	ExecutionsService,
	GroupBy,
	MonitoringService,
	OAuthService,
	PermissionsService,
	ServiceAccountsService,
	SystemService,
	ToolkitsService,
	type AgentResponse,
	type AuditResponse,
	type EventResponse,
	type OAuthGrantResponse,
	type ServiceAccountResponse,
} from '@/shared/api';
import {
	agentToEntity,
	serviceAccountToEntity,
	type AgentEntity,
	type ApiKeyHistoryEntry,
	type ApiKeyInfoEntity,
	type ApiKeyResult,
	type InstanceIdentityEntity,
	type LinkableToolkit,
	type McpLastSeen,
	type McpSessionEntity,
	type OAuthGrantEntity,
	type PermissionCatalogEntry,
	type ServiceAccountEntity,
	type ToolkitBindingEntity,
} from '@/modules/agents/api/types';
import { listAccessRequests, type AccessRequest } from '@/shared/lib';

/**
 * Sentinel error for Agents repository calls. Hooks/components branch on
 * `error instanceof AgentsApiError` without importing the generated `ApiError`.
 * `status` is null for network/parse failures that never reached the server.
 */
export class AgentsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'AgentsApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toAgentsError(error: unknown, fallback: string): AgentsApiError {
	if (error instanceof ApiError) {
		const body = error.body as { detail?: unknown } | undefined;
		let detail: string | undefined;
		if (typeof body?.detail === 'string') {
			detail = body.detail;
		} else if (Array.isArray(body?.detail)) {
			// FastAPI 422 validation error: [{ loc, msg, ... }]
			detail = body.detail
				.map((d) => (d as { msg?: string }).msg)
				.filter(Boolean)
				.join('; ');
		}
		return new AgentsApiError(detail || error.message || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new AgentsApiError(error.message || fallback, null, error);
	}
	return new AgentsApiError(fallback, null, error);
}

export interface ListResult<T> {
	entities: T[];
	hasMore: boolean;
	nextCursor: string | null;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export async function listAgents(params: {
	status?: string | null;
	cursor?: string | null;
	limit?: number;
}): Promise<ListResult<AgentEntity>> {
	try {
		const res = await AgentsService.listAgents({
			status: params.status ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
		return {
			entities: res.data.map(agentToEntity),
			hasMore: res.has_more,
			nextCursor: res.next_cursor ?? null,
		};
	} catch (error) {
		throw toAgentsError(error, 'Failed to load agents.');
	}
}

export async function getAgent(agentId: string): Promise<AgentEntity> {
	try {
		return agentToEntity(await AgentsService.getAgent({ agentId }));
	} catch (error) {
		throw toAgentsError(error, 'Failed to load the agent.');
	}
}

export async function approveAgent(agentId: string): Promise<AgentEntity> {
	try {
		return agentToEntity(await AgentsService.approveAgent({ agentId }));
	} catch (error) {
		throw toAgentsError(error, 'Failed to approve the agent.');
	}
}

export async function denyAgent(agentId: string, reason: string): Promise<AgentEntity> {
	try {
		const res: AgentResponse = await AgentsService.denyAgent({
			agentId,
			requestBody: { reason },
		});
		return agentToEntity(res);
	} catch (error) {
		throw toAgentsError(error, 'Failed to deny the agent.');
	}
}

export async function disableAgent(agentId: string): Promise<void> {
	try {
		await AgentsService.disableAgent({ agentId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to disable the agent.');
	}
}

export async function enableAgent(agentId: string): Promise<void> {
	try {
		await AgentsService.enableAgent({ agentId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to enable the agent.');
	}
}

export async function archiveAgent(agentId: string): Promise<void> {
	try {
		await AgentsService.archiveAgent({ agentId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to archive the agent.');
	}
}

export async function listAgentToolkits(agentId: string): Promise<ToolkitBindingEntity[]> {
	try {
		const res = await AgentsService.listAgentToolkits({ agentId });
		return res.data.map((b) => ({
			id: b.id,
			toolkitId: b.toolkit_id,
			boundAt: b.bound_at,
		}));
	} catch (error) {
		throw toAgentsError(error, 'Failed to load bound toolkits.');
	}
}

/**
 * Candidate toolkits for the agent-side "Bind toolkit" picker (#607). Reads the
 * org-wide ``GET /toolkits`` surface through the shared API — the agents module
 * must not import the toolkits module (module-boundary rule), so it maps the
 * shared ``ToolkitResponse`` into a small picker shape here.
 *
 * Paginates via ``cursor``/``has_more`` so a workspace with more than one page
 * of toolkits (default page size 50) still lists everything — a hardcoded
 * ``limit`` would silently drop the tail. A hard page cap
 * keeps a runaway/misconfigured backend from looping forever. Kill-switched
 * toolkits are *included* here (``active`` is carried through); the picker
 * itself refuses to select them so a broken binding can't be created — but
 * keeping them in the list lets callers show them as a
 * disabled row with a "suspended" affordance, which is easier to reason about
 * than a silently-missing toolkit.
 *
 * Defensive against a misbehaving backend: we break if a cursor repeats (a
 * pagination loop) and dedupe the accumulated rows by ``toolkitId`` so a page
 * that re-emits an earlier row can't produce duplicate picker entries.
 */
export async function listLinkableToolkits(): Promise<LinkableToolkit[]> {
	try {
		const out: LinkableToolkit[] = [];
		const seenToolkitIds = new Set<string>();
		const seenCursors = new Set<string>();
		let cursor: string | null = null;
		const MAX_PAGES = 20;
		for (let page = 0; page < MAX_PAGES; page += 1) {
			const res = await ToolkitsService.listToolkits({ cursor, limit: 100 });
			for (const t of res.data) {
				if (seenToolkitIds.has(t.toolkit_id)) continue;
				seenToolkitIds.add(t.toolkit_id);
				out.push({ toolkitId: t.toolkit_id, name: t.name, active: t.active });
			}
			if (!res.has_more || !res.next_cursor) break;
			// A repeated cursor means the backend is looping — stop rather than
			// re-fetch the same page until MAX_PAGES.
			if (seenCursors.has(res.next_cursor)) break;
			seenCursors.add(res.next_cursor);
			cursor = res.next_cursor;
		}
		return out;
	} catch (error) {
		throw toAgentsError(error, 'Failed to load toolkits.');
	}
}

/**
 * Resolve a single toolkit's human name (`GET /toolkits/{id}`). Powers the
 * per-row name lookup on the agent detail page's "Bound toolkits" card: the
 * binding response (`GET /agents/{id}/toolkits`) carries only the toolkit id,
 * so each bound row reads its own name here instead of the whole workspace
 * catalogue paying a paginated sweep on every page load.
 *
 * The name is BEST-EFFORT and purely cosmetic — the row always falls back to
 * the id, and no caller surfaces an error. So any real failure (a since-deleted
 * 404, a transient 5xx, or a network blip) simply returns ``null`` rather than
 * pushing the query into an error state over a display nicety. The ONE
 * exception is an ``AbortError``: React Query throws it to cancel an in-flight
 * request on unmount or key change, so it's re-thrown (not swallowed into a
 * spurious ``null`` result) to let cancellation propagate as intended.
 */
export async function getToolkitName(toolkitId: string): Promise<string | null> {
	try {
		const res = await ToolkitsService.getToolkit({ toolkitId });
		return res?.name ?? null;
	} catch (e) {
		if (e instanceof Error && e.name === 'AbortError') throw e;
		return null;
	}
}

/** Bind a toolkit to an agent (`POST /agents/{id}/toolkits`) — the agent-side
 * mirror of the toolkit page's "Link agent" (#607). */
export async function bindToolkitToAgent(agentId: string, toolkitId: string): Promise<void> {
	try {
		await AgentsService.bindToolkit({ agentId, requestBody: { toolkit_id: toolkitId } });
	} catch (error) {
		throw toAgentsError(error, 'Failed to bind the toolkit.');
	}
}

/** Unbind a toolkit from an agent (`DELETE /agents/{id}/toolkits/{toolkit_id}`). */
export async function unbindToolkitFromAgent(agentId: string, toolkitId: string): Promise<void> {
	try {
		await AgentsService.unbindToolkit({ agentId, toolkitId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to unbind the toolkit.');
	}
}

export async function createAgent(params: {
	name: string;
	description?: string | null;
	scopes?: string[] | null;
}): Promise<AgentEntity> {
	try {
		const res = await AgentsService.createAgent({
			requestBody: {
				name: params.name,
				description: params.description ?? null,
				// Optional initial grants — POST /agents accepts scopes[] so a
				// manually created agent can start with the permissions it needs
				// instead of a follow-up PUT from the detail page.
				scopes: params.scopes?.length ? params.scopes : null,
			},
		});
		return agentToEntity(res);
	} catch (error) {
		throw toAgentsError(error, 'Failed to create the agent.');
	}
}

/** Fields an operator may edit in place (PATCH /agents/{id}). */
export interface AgentPatch {
	name?: string;
	description?: string | null;
	ownerId?: string | null;
}

/**
 * Partially update an agent — name, description, or owner. Only the provided
 * keys are sent (PATCH semantics: an omitted key is left untouched, an
 * explicit `null` clears the field where the backend allows it).
 */
export async function updateAgent(agentId: string, patch: AgentPatch): Promise<AgentEntity> {
	try {
		const res = await AgentsService.updateAgent({
			agentId,
			requestBody: {
				...(patch.name !== undefined ? { name: patch.name } : {}),
				...(patch.description !== undefined ? { description: patch.description } : {}),
				...(patch.ownerId !== undefined ? { owner_id: patch.ownerId } : {}),
			},
		});
		return agentToEntity(res);
	} catch (error) {
		throw toAgentsError(error, 'Failed to update the agent.');
	}
}

export async function generateAgentApiKey(agentId: string): Promise<ApiKeyResult> {
	try {
		const res = await AgentsService.generateAgentApiKey({ agentId });
		return { key: res.key };
	} catch (error) {
		throw toAgentsError(error, 'Failed to generate API key.');
	}
}

export async function revokeAgentApiKey(agentId: string): Promise<void> {
	try {
		await AgentsService.revokeAgentApiKey({ agentId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to revoke API key.');
	}
}

export async function getAgentApiKeyInfo(agentId: string): Promise<ApiKeyInfoEntity | null> {
	try {
		const res = await AgentsService.getAgentApiKeyInfo({ agentId });
		if (res == null) return null;
		return {
			id: res.id,
			status: res.status as 'active' | 'revoked',
			createdAt: res.created_at,
			rotatedAt: res.rotated_at ?? null,
			createdBy: res.created_by ?? null,
		};
	} catch (error) {
		throw toAgentsError(error, 'Failed to load API key info.');
	}
}

export async function getAgentApiKeyHistory(agentId: string): Promise<ApiKeyHistoryEntry[]> {
	try {
		const res = await AgentsService.getAgentApiKeyHistory({ agentId });
		return res.data.map((e) => ({
			id: e.id,
			action: e.action,
			reason: e.reason ?? null,
			actorId: e.actor_id ?? null,
			occurredAt: e.occurred_at,
		}));
	} catch (error) {
		throw toAgentsError(error, 'Failed to load API key history.');
	}
}

export async function generateServiceAccountApiKey(
	serviceAccountId: string,
): Promise<ApiKeyResult> {
	try {
		const res = await ServiceAccountsService.generateServiceAccountApiKey({
			serviceAccountId,
		});
		return { key: res.key };
	} catch (error) {
		throw toAgentsError(error, 'Failed to generate API key.');
	}
}

// ---------------------------------------------------------------------------
// Service accounts
// ---------------------------------------------------------------------------

export async function listServiceAccounts(params: {
	status?: string | null;
	cursor?: string | null;
	limit?: number;
}): Promise<ListResult<ServiceAccountEntity>> {
	try {
		const res = await ServiceAccountsService.listServiceAccounts({
			status: params.status ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
		return {
			entities: res.data.map(serviceAccountToEntity),
			hasMore: res.has_more,
			nextCursor: res.next_cursor ?? null,
		};
	} catch (error) {
		throw toAgentsError(error, 'Failed to load service accounts.');
	}
}

export async function createServiceAccount(params: {
	name: string;
	description?: string | null;
	scopes?: string[] | null;
}): Promise<ServiceAccountEntity> {
	try {
		const res: ServiceAccountResponse = await ServiceAccountsService.createServiceAccount({
			requestBody: {
				name: params.name,
				description: params.description ?? null,
				// Optional initial grants (mirrors createAgent).
				scopes: params.scopes?.length ? params.scopes : null,
			},
		});
		return serviceAccountToEntity(res);
	} catch (error) {
		throw toAgentsError(error, 'Failed to create the service account.');
	}
}

export async function getServiceAccount(serviceAccountId: string): Promise<ServiceAccountEntity> {
	try {
		return serviceAccountToEntity(
			await ServiceAccountsService.getServiceAccount({
				serviceAccountId,
			}),
		);
	} catch (error) {
		throw toAgentsError(error, 'Failed to load the service account.');
	}
}

export async function approveServiceAccount(
	serviceAccountId: string,
): Promise<ServiceAccountEntity> {
	try {
		return serviceAccountToEntity(
			await ServiceAccountsService.approveServiceAccount({
				serviceAccountId,
			}),
		);
	} catch (error) {
		throw toAgentsError(error, 'Failed to approve the service account.');
	}
}

export async function denyServiceAccount(
	serviceAccountId: string,
	reason: string,
): Promise<ServiceAccountEntity> {
	try {
		return serviceAccountToEntity(
			await ServiceAccountsService.denyServiceAccount({
				serviceAccountId,
				requestBody: { reason },
			}),
		);
	} catch (error) {
		throw toAgentsError(error, 'Failed to deny the service account.');
	}
}

export async function disableServiceAccount(serviceAccountId: string): Promise<void> {
	try {
		await ServiceAccountsService.disableServiceAccount({
			serviceAccountId,
		});
	} catch (error) {
		throw toAgentsError(error, 'Failed to disable the service account.');
	}
}

export async function enableServiceAccount(serviceAccountId: string): Promise<void> {
	try {
		await ServiceAccountsService.enableServiceAccount({
			serviceAccountId,
		});
	} catch (error) {
		throw toAgentsError(error, 'Failed to enable the service account.');
	}
}

export async function archiveServiceAccount(serviceAccountId: string): Promise<void> {
	try {
		await ServiceAccountsService.archiveServiceAccount({
			serviceAccountId,
		});
	} catch (error) {
		throw toAgentsError(error, 'Failed to archive the service account.');
	}
}

// ---------------------------------------------------------------------------
// Scopes (#615) — platform permission catalogue + per-actor scope grants.
//
// Two scope vocabularies exist in this codebase; these are the PLATFORM
// permission scopes (`org:admin`, `service-accounts:write`, …) drawn from
// `GET /permissions` — NOT the OAuth2 provider scopes the credentials picker
// uses. `PUT .../scopes` replaces the entire set (no partial grant/revoke), so
// callers read the full list, edit it, and write it back.
// ---------------------------------------------------------------------------

export async function listPermissions(): Promise<PermissionCatalogEntry[]> {
	try {
		const res = await PermissionsService.listPermissions();
		return res.data.map((p) => ({
			name: p.name,
			description: p.description,
			implies: p.implies,
			grantableByCaller: p.grantable_by_caller,
		}));
	} catch (error) {
		throw toAgentsError(error, 'Failed to load the permission catalogue.');
	}
}

export async function getAgentScopes(agentId: string): Promise<string[]> {
	try {
		const res = await AgentsService.getAgentScopes({ agentId });
		return res.scopes;
	} catch (error) {
		throw toAgentsError(error, "Failed to load the agent's scopes.");
	}
}

export async function replaceAgentScopes(agentId: string, scopes: string[]): Promise<string[]> {
	try {
		const res = await AgentsService.replaceAgentScopes({
			agentId,
			requestBody: { scopes },
		});
		return res.scopes;
	} catch (error) {
		throw toAgentsError(error, "Failed to update the agent's scopes.");
	}
}

export async function getServiceAccountScopes(serviceAccountId: string): Promise<string[]> {
	try {
		const res = await ServiceAccountsService.getServiceAccountScopes({
			serviceAccountId,
		});
		return res.scopes;
	} catch (error) {
		throw toAgentsError(error, "Failed to load the service account's scopes.");
	}
}

export async function replaceServiceAccountScopes(
	serviceAccountId: string,
	scopes: string[],
): Promise<string[]> {
	try {
		const res = await ServiceAccountsService.replaceServiceAccountScopes({
			serviceAccountId,
			requestBody: { scopes },
		});
		return res.scopes;
	} catch (error) {
		throw toAgentsError(error, "Failed to update the service account's scopes.");
	}
}

// ---------------------------------------------------------------------------
// Fleet usage (GET /monitoring/usage?group_by=agent)
//
// The same aggregate the Monitor page and the enterprise console read, sliced
// per actor for the fleet table's activity columns. The endpoint is gated on
// `org:admin`; a 403 is an expected outcome for non-admin operators, not an
// error — the caller hides the columns entirely.
// ---------------------------------------------------------------------------

/** One actor's execution stats over the query window. */
export interface ActorUsage {
	total: number;
	success: number;
	failed: number;
	/** Executions per aggregate bucket, oldest → newest (sparkline-ready). */
	trend: number[];
}

/**
 * Per-actor usage over the trailing `sinceDays` window, keyed by actor id.
 * Backend `top` keys are mechanical `actor_type/actor_id` strings; rows for
 * other actor types (users, unattributed NULLs) are dropped here. Returns
 * `null` on 403 — the caller renders no activity columns for non-admins.
 *
 * The aggregate is a top-N leaderboard capped at 50 by the backend
 * (`GET /monitoring/usage` validates `top_limit <= 50`), so actors absent
 * from the map are "not in the top 50", NOT "zero executions" — callers must
 * render the distinction (em-dash, not 0).
 */
export async function fetchActorsUsage(
	actorType: 'agent' | 'service_account',
	sinceDays = 7,
): Promise<Map<string, ActorUsage> | null> {
	try {
		// Window bounds ceiled to the next minute: the backend's aggregate uses
		// a strict `started_at < until`, so a floored/now bound hides the
		// current partial minute (#913); a fixed until also keeps the window an
		// exact multiple of the bucket tier and the server cache key stable for
		// a whole minute (a per-second `since` defeated it entirely).
		const until = (Math.floor(Date.now() / 60_000) + 1) * 60;
		const res = await MonitoringService.getUsageStats({
			since: until - sinceDays * 86400,
			until,
			groupBy: GroupBy.AGENT,
			topLimit: 50,
		});
		const prefix = `${actorType}/`;
		const usage = new Map<string, ActorUsage>();
		for (const row of res.top) {
			if (!row.key?.startsWith(prefix)) continue;
			usage.set(row.key.slice(prefix.length), {
				total: row.total,
				success: row.success,
				failed: row.failed,
				trend: row.trend,
			});
		}
		return usage;
	} catch (error) {
		if (error instanceof ApiError && error.status === 403) return null;
		throw toAgentsError(error, 'Failed to load usage statistics.');
	}
}

/** One time bucket of an actor's execution volume. */
export interface UsageBucketEntity {
	/** Bucket start, unix seconds. */
	ts: number;
	total: number;
	success: number;
	failed: number;
}

/** A single actor's execution stats + volume series over the query window. */
export interface ActorUsageDetail {
	total: number;
	success: number;
	failed: number;
	/** Width of each bucket in seconds (drives axis label formatting). */
	bucketSeconds: number;
	/** Sparse: only buckets with data (no zero-fill), oldest → newest. */
	buckets: UsageBucketEntity[];
}

/**
 * One actor's usage over the trailing `sinceDays` window — the detail page's
 * KPI strip and Activity chart. `agent_id` is the endpoint's (misnamed) actor
 * filter: the backend maps it onto `actor_id`, so it works for service
 * accounts too. Same 403 contract as `fetchActorsUsage`: `null` means the
 * viewer isn't an admin and the caller renders no stats — never an error.
 */
export async function fetchActorUsageDetail(
	actorId: string,
	sinceDays = 7,
): Promise<ActorUsageDetail | null> {
	try {
		// Next-minute-ceiled bounds — see fetchActorsUsage: includes the current
		// partial minute (#913, the volume chart must never trail the
		// executions feed) with a cache-stable, tier-exact window.
		const until = (Math.floor(Date.now() / 60_000) + 1) * 60;
		const res = await MonitoringService.getUsageStats({
			since: until - sinceDays * 86400,
			until,
			agentId: actorId,
			// `top` is irrelevant here (the window is already one actor).
			topLimit: 1,
		});
		return {
			total: res.stats.total,
			success: res.stats.success,
			failed: res.stats.failed,
			bucketSeconds: res.bucket_seconds,
			buckets: res.buckets.map((b) => ({
				ts: b.ts,
				total: b.total,
				success: b.success,
				failed: b.failed,
			})),
		};
	} catch (error) {
		if (error instanceof ApiError && error.status === 403) return null;
		throw toAgentsError(error, 'Failed to load usage statistics.');
	}
}

/** One row of an actor's execution feed (a trimmed `ExecutionResponse`). */
export interface ActorExecutionEntity {
	id: string;
	status: string;
	toolkitId: string;
	toolkitName: string | null;
	operationId: string | null;
	durationMs: number | null;
	httpStatus: number | null;
	error: string | null;
	startedAt: string;
}

/**
 * The most recent executions attributed to one actor
 * (`GET /executions?actor_id=…`). One page only — the detail page shows a
 * recent-activity feed and deep-links to Monitor (which owns cursor paging,
 * filters, and trace sheets) for the full history. `null` on 403.
 */
export async function fetchActorExecutions(
	actorId: string,
	limit = 10,
): Promise<{ items: ActorExecutionEntity[]; hasMore: boolean } | null> {
	try {
		const res = await ExecutionsService.listExecutions({ actorId, limit });
		return {
			items: res.data.map((r) => ({
				id: r.execution_id,
				status: r.status,
				toolkitId: r.toolkit_id,
				toolkitName: r.toolkit_name ?? null,
				operationId: r.operation_id ?? null,
				durationMs: r.duration_ms ?? null,
				httpStatus: r.http_status ?? null,
				error: r.error ?? null,
				startedAt: r.started_at,
			})),
			hasMore: res.has_more,
		};
	} catch (error) {
		if (error instanceof ApiError && error.status === 403) return null;
		throw toAgentsError(error, 'Failed to load executions.');
	}
}

// ---------------------------------------------------------------------------
// Access requests filed BY an actor (#619).
//
// An access request carries `actor_id` set to the filer's identity; for an
// agent/service account that's the actor's own id. `GET /access-requests`
// already filters by it, so the per-actor view is a thin read over the shared
// access-request repository (`@/shared/lib`) — the same cross-cutting tier the
// dashboard card and Agent Rail use. No new backend surface. The decide flow is
// the shared `AccessRequestDialog`; this just lists what's still pending.
// ---------------------------------------------------------------------------

/** The access requests an actor has filed that are still in `status` (default pending). */
export async function fetchActorAccessRequests(
	actorId: string,
	status: string | null = 'pending',
): Promise<AccessRequest[]> {
	try {
		const page = await listAccessRequests({ actorId, status, limit: 50 });
		return page.data;
	} catch (error) {
		throw toAgentsError(error, "Failed to load the actor's access requests.");
	}
}

// ---------------------------------------------------------------------------
// Audit (read-only actor-scoped lens on the shared /audit endpoint).
// ---------------------------------------------------------------------------

/** One audit-log row targeting this actor — the generated model, re-exported
 * so hooks/components never touch the facade directly. */
export type ActorAuditEntry = AuditResponse;

/**
 * Actor-scoped audit entries — the lifecycle trail recorded against this
 * agent / service account as the TARGET (register, approve/deny, disable/
 * enable, key rotation, toolkit grant/revoke). Mirrors the toolkit console's
 * `listToolkitAudit`. Requires `org:admin`; 401/403 map to an empty list so
 * the "Recent changes" panel degrades gracefully for non-admins.
 */
export async function listActorAudit(
	actorKind: 'agent' | 'service-account',
	actorId: string,
	limit = 25,
): Promise<AuditResponse[]> {
	try {
		const res = await AuditService.listAuditEntries({
			targetType:
				actorKind === 'agent' ? AuditTargetType.AGENT : AuditTargetType.SERVICE_ACCOUNT,
			targetId: actorId,
			limit,
		});
		return res.data;
	} catch (error) {
		if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
			return [];
		}
		throw toAgentsError(error, 'Failed to load the audit log.');
	}
}

// ---------------------------------------------------------------------------
// MCP transport visibility (local-MCP 2-E2, #1188).
//
// No new backend surface: the sessions read is the existing
// `GET /events?event_type=mcp.session_started[&actor_id=…]` (behind
// `events:read`), last-active is the latest MCP-origin execution
// (`GET /executions?origin=mcp&actor_id=…`), and instance identity is the
// unauthenticated `GET /instance`. Event reads follow the enrichment degrade
// contract (`fetchActorsUsage`): 401/403 resolve to `null` and the caller
// hides the surface — a permission gate is not an error.
// ---------------------------------------------------------------------------

/** Wire value of the MCP session event type (`EventType.MCP_SESSION_STARTED`). */
export const MCP_SESSION_STARTED_EVENT = 'mcp.session_started';

/** The `origin` wire value stamped on MCP executions (`Origin.MCP`). */
export const MCP_ORIGIN = 'mcp';

function eventToMcpSession(e: EventResponse): McpSessionEntity {
	// The emitter writes clientInfo + transport + session id into the internal
	// event's `data` (two-plane pattern: the telemetry wire is property-free,
	// the UI reads the events table). `data` is a free JSON bag on the wire, so
	// read defensively — a missing key degrades to null, never a crash.
	const data = (e.data ?? {}) as Record<string, unknown>;
	const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
	return {
		eventId: e.event_id,
		sessionId: str(data.session_id),
		transport: str(data.transport),
		clientName: str(data.client_name),
		clientVersion: str(data.client_version),
		startedAt: e.created_at,
	};
}

/**
 * One agent's MCP session history, newest first (single page — the backend
 * caps `limit` at 100; older sessions fall off, which is fine for a
 * recent-history card). `null` when events are permission-gated (401/403).
 */
export async function fetchMcpSessions(actorId: string): Promise<McpSessionEntity[] | null> {
	try {
		const res = await EventsService.listEvents({
			eventType: [MCP_SESSION_STARTED_EVENT],
			actorId,
			limit: 100,
		});
		return res.data.map(eventToMcpSession);
	} catch (error) {
		if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
			return null;
		}
		throw toAgentsError(error, 'Failed to load MCP sessions.');
	}
}

/**
 * Latest MCP session per agent for the roster's "last seen via MCP" cell,
 * from ONE page of `mcp.session_started` events. The feed is newest-first, so
 * the first row per `actor_id` is that agent's latest session. Like the
 * usage top-50 leaderboard, this is bounded enrichment: an agent absent from
 * the newest 100 session events means "no recent MCP session known", not
 * "never" — callers render an em-dash. `null` when permission-gated.
 */
export async function fetchMcpLastSeenByActor(): Promise<Map<string, McpLastSeen> | null> {
	try {
		const res = await EventsService.listEvents({
			eventType: [MCP_SESSION_STARTED_EVENT],
			limit: 100,
		});
		const out = new Map<string, McpLastSeen>();
		for (const e of res.data) {
			if (!e.actor_id || out.has(e.actor_id)) continue;
			const s = eventToMcpSession(e);
			out.set(e.actor_id, {
				clientName: s.clientName,
				clientVersion: s.clientVersion,
				startedAt: s.startedAt,
			});
		}
		return out;
	} catch (error) {
		if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
			return null;
		}
		throw toAgentsError(error, 'Failed to load MCP session events.');
	}
}

/**
 * When this agent last executed over MCP (`started_at` of the newest
 * MCP-origin execution) — the "last active" half of the sessions card's
 * "started / last active" story. `null` result covers both "no MCP
 * executions yet" and the 403 gate; the caller renders a quiet dash either
 * way, so the two need no distinct copy.
 */
export async function fetchLatestMcpActivity(actorId: string): Promise<string | null> {
	try {
		const res = await ExecutionsService.listExecutions({
			actorId,
			origin: MCP_ORIGIN,
			limit: 1,
		});
		return res.data[0]?.started_at ?? null;
	} catch (error) {
		if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
			return null;
		}
		throw toAgentsError(error, 'Failed to load MCP activity.');
	}
}

/**
 * This backend's self-described identity (`GET /instance`, unauthenticated) —
 * the config card shows which instance a pasted snippet registers against.
 */
export async function fetchInstanceIdentity(): Promise<InstanceIdentityEntity> {
	try {
		const res = await SystemService.getInstance();
		return {
			backend: res.backend,
			baseUrl: res.canonical_base_url,
			host: res.host,
		};
	} catch (error) {
		throw toAgentsError(error, 'Failed to load the instance identity.');
	}
}

// ---------------------------------------------------------------------------
// OAuth consent grants (phase-3a §4.8) — the "Connected clients" panel.
// ---------------------------------------------------------------------------

function grantToEntity(r: OAuthGrantResponse): OAuthGrantEntity {
	return {
		id: r.id,
		oauthClientId: r.oauth_client_id,
		clientName: r.client_name ?? null,
		clientOrigin: r.client_origin ?? null,
		userId: r.user_id,
		agentId: r.agent_id,
		scopes: r.scopes,
		status: r.status,
		createdAt: r.created_at,
		revokedAt: r.revoked_at ?? null,
		lastUsedAt: r.last_used_at ?? null,
	};
}

/**
 * The OAuth clients holding a consent→agent grant on this agent
 * (`GET /agents/{id}/oauth-grants`, owner-or-admin). `status` narrows to
 * active/revoked; null returns the full history.
 */
export async function listAgentOauthGrants(
	agentId: string,
	status: 'active' | 'revoked' | null = 'active',
): Promise<ListResult<OAuthGrantEntity>> {
	try {
		const res = await AgentsService.listAgentOauthGrants({
			agentId,
			status,
		});
		return {
			entities: res.data.map(grantToEntity),
			hasMore: res.has_more,
			nextCursor: res.next_cursor ?? null,
		};
	} catch (error) {
		throw toAgentsError(error, "Failed to load the agent's connected clients.");
	}
}

/**
 * Revoke one consent→agent grant (`POST /oauth-grants/{id}:revoke`) — the
 * per-grant kill switch (§4.6). The backend also revokes every access/refresh
 * token minted under the grant; the client's next token use fails closed.
 */
export async function revokeOauthGrant(grantId: string): Promise<void> {
	try {
		await OAuthService.revokeOauthGrant({ grantId });
	} catch (error) {
		throw toAgentsError(error, 'Failed to revoke the grant.');
	}
}
