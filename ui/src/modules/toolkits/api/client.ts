/**
 * Toolkits repository tier (≈ backend Repository layer).
 *
 * The ONLY place in the Toolkits module that talks to `@/shared/api` (the HTTP
 * facade) and the generated services. Views and hooks never import the facade
 * directly — ESLint enforces this (ui/eslint.config.js "Layering"). Each
 * function turns a typed service call into UI-shaped data and normalizes errors
 * into a single sentinel (`ToolkitsApiError`) the service tier branches on.
 *
 * Every endpoint here is REAL and verified against the live `/openapi.json`
 * (`control/web/routers/toolkits.py` + `auth/web/routers/agents.py`). No MSW
 * gap-filling — see jentic-one-ui-migration/API-CONTRACT.md §3.
 */
import {
	ApiError,
	ToolkitsService,
	ToolkitKeysService,
	ToolkitCredentialsService,
	ToolkitPermissionsService,
	CredentialsService,
	AgentsService,
	AuditService,
	AuditTargetType,
	type ToolkitResponse,
	type ToolkitListResponse,
	type ToolkitCreateRequest,
	type ToolkitUpdateRequest,
	type ToolkitKeyResponse,
	type ToolkitKeyListResponse,
	type ToolkitKeyCreateRequest,
	type ToolkitKeyCreateResponse,
	type ToolkitKeyUpdateRequest,
	type ToolkitCredentialBindingResponse,
	type ToolkitCredentialListResponse,
	type ToolkitCredentialBindRequest,
	type PermissionRuleListResponse,
	type PermissionsPatchRequest,
	type PermissionRuleSchema,
	type ToolkitBindingResponse,
	type AuditResponse,
} from '@/shared/api';
import type {
	BindableCredential,
	CreatedToolkit,
	ToolkitAgent,
} from '@/modules/toolkits/api/types';

/**
 * Sentinel error for Toolkits repository calls. Hooks/components branch on
 * `error instanceof ToolkitsApiError` without importing the generated
 * `ApiError`. `status` is null for network/parse failures that never reached
 * the server.
 */
export class ToolkitsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'ToolkitsApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toToolkitsError(error: unknown, fallback: string): ToolkitsApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new ToolkitsApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new ToolkitsApiError(error.message || fallback, null, error);
	}
	return new ToolkitsApiError(fallback, null, error);
}

// --- Toolkit CRUD ---------------------------------------------------------

export async function listToolkits(params: {
	cursor?: string | null;
	limit?: number;
}): Promise<ToolkitListResponse> {
	try {
		return await ToolkitsService.listToolkits({
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load toolkits.');
	}
}

export async function getToolkit(toolkitId: string): Promise<ToolkitResponse> {
	try {
		return await ToolkitsService.getToolkit({ toolkitId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load toolkit.');
	}
}

export async function createToolkit(body: ToolkitCreateRequest): Promise<CreatedToolkit> {
	try {
		const res = await ToolkitsService.createToolkit({ requestBody: body });
		return { toolkit: res.toolkit, apiKey: res.api_key };
	} catch (error) {
		throw toToolkitsError(error, 'Failed to create toolkit.');
	}
}

export async function updateToolkit(
	toolkitId: string,
	body: ToolkitUpdateRequest,
): Promise<ToolkitResponse> {
	try {
		return await ToolkitsService.updateToolkit({
			toolkitId,
			requestBody: body,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to update toolkit.');
	}
}

/**
 * Suspend / restore a toolkit via its `active` flag — the kill switch. This is
 * the reversible option: the broker rejects every call against an inactive
 * toolkit until it's restored. For a permanent removal use `deleteToolkit`
 * below.
 */
export async function setToolkitActive(
	toolkitId: string,
	active: boolean,
): Promise<ToolkitResponse> {
	return updateToolkit(toolkitId, { active });
}

/**
 * Hard-delete a toolkit (`DELETE /toolkits/{id}`). Cascades server-side to its
 * keys, credential bindings, and permission rules — irreversible. Distinct
 * from the kill switch (`setToolkitActive`), which is reversible.
 */
export async function deleteToolkit(toolkitId: string): Promise<void> {
	try {
		await ToolkitsService.deleteToolkit({ toolkitId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to delete toolkit.');
	}
}

// --- Keys -----------------------------------------------------------------

export async function listKeys(toolkitId: string): Promise<ToolkitKeyListResponse> {
	try {
		return await ToolkitKeysService.listKeys({ toolkitId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load API keys.');
	}
}

export async function createKey(
	toolkitId: string,
	body: ToolkitKeyCreateRequest,
): Promise<ToolkitKeyCreateResponse> {
	try {
		return await ToolkitKeysService.createKey({
			toolkitId,
			requestBody: body,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to create API key.');
	}
}

export async function updateKey(
	toolkitId: string,
	keyId: string,
	body: ToolkitKeyUpdateRequest,
): Promise<ToolkitKeyResponse> {
	try {
		return await ToolkitKeysService.updateKey({
			toolkitId,
			keyId,
			requestBody: body,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to update API key.');
	}
}

export async function deleteKey(toolkitId: string, keyId: string): Promise<void> {
	try {
		await ToolkitKeysService.deleteKey({ toolkitId, keyId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to revoke API key.');
	}
}

// --- Credential bindings --------------------------------------------------

export async function listBindings(toolkitId: string): Promise<ToolkitCredentialListResponse> {
	try {
		return await ToolkitCredentialsService.listBindings({ toolkitId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load bound credentials.');
	}
}

export async function bindCredential(
	toolkitId: string,
	body: ToolkitCredentialBindRequest,
): Promise<ToolkitCredentialBindingResponse> {
	try {
		return await ToolkitCredentialsService.bindCredential({
			toolkitId,
			requestBody: body,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to bind credential.');
	}
}

export async function unbindCredential(toolkitId: string, credentialId: string): Promise<void> {
	try {
		await ToolkitCredentialsService.unbindCredential({
			toolkitId,
			credentialId,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to unbind credential.');
	}
}

/**
 * List the workspace credentials that can be bound to a toolkit.
 *
 * Reads the org-wide `GET /credentials` surface (the same endpoint the
 * Credentials module owns) so the bind dialog can offer a searchable picker
 * instead of asking the user to paste a raw `cred_…` id. We can't import the
 * sibling Credentials module (ESLint module-boundary rule), so the call goes
 * through the shared generated `CredentialsService` here in the repository tier
 * — the one sanctioned place that touches `@/shared/api`.
 *
 * The list is projected to a minimal `BindableCredential` (id + name + api +
 * type) so view code never depends on the full redacted credential model.
 */
export async function listBindableCredentials(): Promise<BindableCredential[]> {
	try {
		const res = await CredentialsService.listCredentials({ limit: 100 });
		return res.data.map((c) => ({
			credential_id: c.credential_id,
			name: c.name,
			type: c.type,
			vendor: c.api?.vendor ?? null,
			provider: c.provider ?? null,
		}));
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load credentials.');
	}
}

// --- Per-binding permission rules ----------------------------------------

export async function listPermissions(
	toolkitId: string,
	credentialId: string,
): Promise<PermissionRuleListResponse> {
	try {
		return await ToolkitPermissionsService.listToolkitPermissions({ toolkitId, credentialId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load permissions.');
	}
}

export async function replacePermissions(
	toolkitId: string,
	credentialId: string,
	rules: PermissionRuleSchema[],
): Promise<PermissionRuleListResponse> {
	try {
		return await ToolkitPermissionsService.replacePermissions({
			toolkitId,
			credentialId,
			requestBody: rules,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to save permissions.');
	}
}

export async function patchPermissions(
	toolkitId: string,
	credentialId: string,
	body: PermissionsPatchRequest,
): Promise<PermissionRuleListResponse> {
	try {
		return await ToolkitPermissionsService.patchPermissions({
			toolkitId,
			credentialId,
			requestBody: body,
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to update permissions.');
	}
}

// --- Agent bindings (agent side, on the /agents router → AgentsService) ---

/**
 * Agents bound to a toolkit — the reverse lookup, served by the toolkits
 * router's `GET /toolkits/{id}/agents`. Projected to a minimal `ToolkitAgent`
 * (id + name + status + bound_at) so view code never depends on the wire model.
 */
export async function listToolkitAgents(toolkitId: string): Promise<ToolkitAgent[]> {
	try {
		const res = await ToolkitsService.listToolkitAgents({ toolkitId, limit: 100 });
		return res.data.map((row) => ({
			agent_id: row.agent_id,
			agent_name: row.agent_name,
			status: row.status,
			bound_at: row.bound_at,
		}));
	} catch (error) {
		throw toToolkitsError(error, "Failed to load the toolkit's agents.");
	}
}

/**
 * All agents in the workspace — powers the "Link agent" picker. Reads the
 * org-wide `GET /agents` surface through the shared API (no sibling Agents
 * module import). Projected to the same minimal `ToolkitAgent` shape; the
 * binding-specific `bound_at` is null here since these aren't bindings.
 */
export async function listLinkableAgents(): Promise<ToolkitAgent[]> {
	try {
		const res = await AgentsService.listAgents({ limit: 100 });
		return res.data.map((a) => ({
			agent_id: a.id,
			agent_name: a.name,
			status: a.status,
			bound_at: null,
		}));
	} catch (error) {
		throw toToolkitsError(error, 'Failed to load agents.');
	}
}

export async function listAgentToolkits(agentId: string): Promise<ToolkitBindingResponse[]> {
	try {
		const res = await AgentsService.listAgentToolkits({ agentId });
		return res.data;
	} catch (error) {
		throw toToolkitsError(error, "Failed to load the agent's toolkits.");
	}
}

export async function bindToolkitToAgent(
	agentId: string,
	toolkitId: string,
): Promise<ToolkitBindingResponse> {
	try {
		return await AgentsService.bindToolkit({
			agentId,
			requestBody: { toolkit_id: toolkitId },
		});
	} catch (error) {
		throw toToolkitsError(error, 'Failed to bind toolkit to agent.');
	}
}

export async function unbindToolkitFromAgent(agentId: string, toolkitId: string): Promise<void> {
	try {
		await AgentsService.unbindToolkit({ agentId, toolkitId });
	} catch (error) {
		throw toToolkitsError(error, 'Failed to unbind toolkit from agent.');
	}
}

// --- Audit (read-only toolkit-scoped lens on the shared /audit endpoint) ---

/**
 * Toolkit-scoped audit entries. The shared `/audit` endpoint filters by a single
 * `target_id`, so this returns the toolkit-level events (create/update/suspend/
 * restore) tagged `target_type=toolkit`. Key- and binding-level sub-events carry
 * the toolkit as `target_parent_id` (no server-side filter for that), so they're
 * surfaced in the org-wide Audit lens (Monitor module) rather than duplicated here.
 * Read-only — requires `org:admin`; a 403 is mapped to an empty result so the
 * panel degrades gracefully for non-admins.
 */
export async function listToolkitAudit(toolkitId: string, limit = 25): Promise<AuditResponse[]> {
	try {
		const res = await AuditService.listAuditEntries({
			targetType: AuditTargetType.TOOLKIT,
			targetId: toolkitId,
			limit,
		});
		return res.data;
	} catch (error) {
		if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
			return [];
		}
		throw toToolkitsError(error, 'Failed to load audit log.');
	}
}
