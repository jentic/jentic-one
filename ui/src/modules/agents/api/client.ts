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
	PermissionsService,
	ServiceAccountsService,
	type AgentResponse,
	type ServiceAccountResponse,
} from '@/shared/api';
import {
	agentToEntity,
	serviceAccountToEntity,
	type AgentEntity,
	type ApiKeyHistoryEntry,
	type ApiKeyInfoEntity,
	type ApiKeyResult,
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

export async function createAgent(params: {
	name: string;
	description?: string | null;
}): Promise<AgentEntity> {
	try {
		const res = await AgentsService.createAgent({
			requestBody: { name: params.name, description: params.description ?? null },
		});
		return agentToEntity(res);
	} catch (error) {
		throw toAgentsError(error, 'Failed to create the agent.');
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
}): Promise<ServiceAccountEntity> {
	try {
		const res: ServiceAccountResponse = await ServiceAccountsService.createServiceAccount({
			requestBody: { name: params.name, description: params.description ?? null },
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
