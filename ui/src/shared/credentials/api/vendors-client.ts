/**
 * Integrations repository tier — the ONLY place in the module that talks
 * HTTP. Views and hooks import this module, not `fetch` directly.
 *
 * The endpoints backing this module were added after the last OpenAPI
 * regeneration, so we hand-call them via `fetch` + `getToken` from
 * `@/shared/api`. Swap to generated services when `make openapi` runs.
 */

import {
	AgentsService,
	CredentialsService,
	IntegrationsService,
	PermissionRuleSetsService,
	getToken,
	problemDetailText,
	type AgentListResponse,
	type ConnectSessionSummaryResponse,
	type PermissionRuleReadSchema,
	type PermissionRuleSchema,
	type RuleSetResponse,
} from '@/shared/api';
import type {
	ConfirmRequest,
	ConfirmResponse,
	ConnectRequest,
	ConnectResponse,
	ReviewSession,
	StatusResponse,
	VendorAuthCapabilities,
	VendorListResponse,
} from '@/shared/credentials/api/vendors-types';

export class IntegrationsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'IntegrationsApiError';
		this.status = status;
		this.cause = cause;
	}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
	const token = await getToken();
	const headers = new Headers(init.headers);
	if (token) headers.set('Authorization', `Bearer ${token}`);
	if (init.body && !headers.has('Content-Type')) {
		headers.set('Content-Type', 'application/json');
	}
	headers.set('Accept', 'application/json');
	let response: Response;
	try {
		response = await fetch(path, { ...init, headers });
	} catch (err) {
		throw new IntegrationsApiError('network error', null, err);
	}
	if (!response.ok) {
		// Errors are RFC 9457 problem+json ({type, title, detail, instance, …});
		// prefer the human-readable ``detail`` over the status text.
		let detail: string | null = null;
		try {
			detail = problemDetailText(await response.json());
		} catch {
			// ignore parse failure — fall back to statusText
		}
		throw new IntegrationsApiError(
			detail ?? (response.statusText || `HTTP ${response.status}`),
			response.status,
		);
	}
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

export function startIntegrationConnect(body: ConnectRequest): Promise<ConnectResponse> {
	return request('/integrations:connect', {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

/**
 * Bind an existing credential to an agent in "start blocked" mode (no
 * rules). Lives here rather than in ``modules/agents/api`` so the
 * post-connect "Bind to more agents" CTA — which is a credentials-
 * domain feature but touches an agent-owned endpoint — can compose it
 * without crossing the module-vs-module boundary. Uses the generated
 * ``AgentsService`` (allowed at this repository tier) so a schema
 * change lands automatically.
 */
export async function bindCredentialToAgentBlocked(
	agentId: string,
	credentialId: string,
): Promise<void> {
	try {
		await AgentsService.bindAgentCredential({
			agentId,
			requestBody: { credential_id: credentialId },
		});
	} catch (err) {
		const status =
			typeof (err as { status?: number })?.status === 'number'
				? (err as { status: number }).status
				: null;
		throw new IntegrationsApiError(
			(err as Error)?.message ?? 'Failed to bind the credential.',
			status,
			err,
		);
	}
}

/**
 * A session-scoped route, with the ``poll_token`` capability on the query
 * string when the caller holds one. Without it the backend authorises the
 * target agent's owner (holding ``credentials:write`` and ``agents:write``)
 * or ``org:admin`` instead; everyone else gets the same 403 as a wrong token.
 */
function sessionUrl(sessionId: string, suffix: string, pollToken?: string): string {
	const path = `/connect-sessions/${encodeURIComponent(sessionId)}${suffix}`;
	return pollToken ? `${path}?poll_token=${encodeURIComponent(pollToken)}` : path;
}

/**
 * Fetch the review data for a connect session. Gated by the session's
 * ``poll_token`` (the self flow holds it from its own ``:connect``) or, with
 * no token, by being the target agent's owner or ``org:admin`` — which is how
 * a human opens an agent's ``?approve=<sid>`` approval link. Missing session,
 * token mismatch and "neither" all surface as 403 — the backend deliberately
 * doesn't distinguish them (no session-id enumeration oracle), so callers
 * treat a 403 as "session gone / not yours".
 */
export function getConnectSession(sessionId: string, pollToken?: string): Promise<ReviewSession> {
	return request(sessionUrl(sessionId, '', pollToken));
}

/**
 * Confirm scopes + rules and kick off the vendor flow. Gated like the review
 * read (403 on mismatch, missing session, or a token-less caller who is not
 * the owner / admin) and shares the ``:connect`` rate bucket (429 possible).
 */
export function confirmConnectSession(
	sessionId: string,
	pollToken: string | undefined,
	body: ConfirmRequest,
): Promise<ConfirmResponse> {
	return request(sessionUrl(sessionId, ':confirm', pollToken), {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export function pollConnectSessionStatus(
	sessionId: string,
	pollToken?: string,
): Promise<StatusResponse> {
	return request(sessionUrl(sessionId, '/status', pollToken));
}

/**
 * Cancel an in-flight connect session. Idempotent — the backend
 * ``:cancel`` route returns 204 even if the session is already gone,
 * so we can fire this from unmount cleanup without needing to check
 * whether the flow already finished.
 */
export function cancelConnectSession(sessionId: string, pollToken?: string): Promise<void> {
	return request(sessionUrl(sessionId, ':cancel', pollToken), { method: 'POST' });
}

/**
 * Fire-and-forget cancel for tab close (``beforeunload``), where a plain
 * ``fetch`` is aborted with the page. A ``keepalive`` fetch outlives the page
 * like ``navigator.sendBeacon`` does, but — unlike a beacon — carries the
 * ``Authorization`` header every route needs, so it also works without a
 * ``poll_token`` (the owner / ``org:admin`` path). Returns whether a request
 * was sent; the response is never observed.
 */
export function cancelConnectSessionOnUnload(sessionId: string, pollToken?: string): boolean {
	if (typeof fetch !== 'function') return false;
	const token = getToken();
	const headers = new Headers({ Accept: 'application/json' });
	if (token) headers.set('Authorization', `Bearer ${token}`);
	void fetch(sessionUrl(sessionId, ':cancel', pollToken), {
		method: 'POST',
		headers,
		keepalive: true,
	}).catch(() => {});
	return true;
}

/** Session states in which an agent is still waiting on a human. */
const OPEN_CONNECT_STATES = ['created', 'polling'] as const;

/** Upper bound per state; open sessions expire fast, so one page holds them. */
const OPEN_CONNECT_PAGE_LIMIT = 200;

/**
 * The connect sessions an agent opened and is still waiting on a human for
 * (``created`` or ``polling``), oldest first. The list is scoped server-side:
 * the caller's own sessions, plus — for a human — those of the agents they own
 * (``org:admin`` sees all). Sessions a human started from their own dialog are
 * dropped: nobody is waiting on them but that same human. Rows never carry the
 * ``poll_token``.
 */
export async function listOpenConnectRequests(): Promise<ConnectSessionSummaryResponse[]> {
	const pages = await Promise.all(
		OPEN_CONNECT_STATES.map((state) =>
			IntegrationsService.listConnectSessions({ state, limit: OPEN_CONNECT_PAGE_LIMIT }),
		),
	);
	return pages
		.flatMap((page) => page.data)
		.filter((s) => s.agent_id != null && s.requested_by_actor_id === s.agent_id)
		.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

export function listVendors(): Promise<VendorListResponse> {
	return request('/vendors');
}

/**
 * One operation as returned by ``GET /apis/{vendor}/{name}/{version}/operations``.
 * Slim projection — the rules-page preview only needs
 * ``(method, path, operation_id)`` to feed the client-side matcher, plus
 * ``name`` for the display label. The full ``OperationSummaryResponse``
 * carries links + tags + description that the preview doesn't render.
 */
export interface VendorOperation {
	operation_id: string;
	method: string;
	path: string;
	name?: string | null;
	description?: string | null;
}

export interface VendorOperationsPage {
	data: VendorOperation[];
	has_more: boolean;
	next_cursor: string | null;
}

/**
 * Fetch a single page of operations for a vendor's imported OpenAPI.
 * Returns ``null`` when the API isn't imported yet (404) — the rules-page
 * preview surfaces that as "operations still importing…" rather than
 * throwing. Callers that need the full list should use
 * ``listAllVendorOperations`` which follows ``next_cursor`` until
 * exhaustion; this single-page variant stays exported for tests + narrow
 * consumers that only need the first page.
 */
async function listVendorOperations(
	vendor: string,
	name: string,
	version: string,
	opts: { cursor?: string | null; limit?: number } = {},
): Promise<VendorOperationsPage | null> {
	const params = new URLSearchParams();
	if (opts.cursor) params.set('cursor', opts.cursor);
	if (opts.limit != null) params.set('limit', String(opts.limit));
	const qs = params.toString();
	const url = `/apis/${encodeURIComponent(vendor)}/${encodeURIComponent(name)}/${encodeURIComponent(version)}/operations${qs ? `?${qs}` : ''}`;
	try {
		return await request<VendorOperationsPage>(url);
	} catch (err) {
		if (err instanceof IntegrationsApiError && err.status === 404) return null;
		throw err;
	}
}

/**
 * Fetch every page of a vendor's operations by following ``next_cursor``
 * until the server returns ``has_more: false``. The rules-page preview
 * needs the complete op list — grouping / autocomplete / matcher all
 * assume they're seeing every op — and vendors like GitHub can carry
 * ~1000 ops which don't fit in a single 200-op page.
 *
 * Returns ``null`` (like the single-page variant) when the API hasn't
 * imported yet; the SPA continues polling.
 */
export async function listAllVendorOperations(
	vendor: string,
	name: string,
	version: string,
): Promise<VendorOperationsPage | null> {
	// Server caps ``limit`` at 200 (see ``list_api_operations``).
	const PAGE_SIZE = 200;
	// Belt-and-braces cap on total pages to keep a runaway cursor loop
	// from hanging the SPA. 200 * 100 = 20000 ops, comfortably past any
	// real vendor.
	const MAX_PAGES = 100;
	const collected: VendorOperation[] = [];
	let cursor: string | null | undefined = null;
	for (let i = 0; i < MAX_PAGES; i++) {
		const page: VendorOperationsPage | null = await listVendorOperations(
			vendor,
			name,
			version,
			{
				cursor,
				limit: PAGE_SIZE,
			},
		);
		if (page == null) return null;
		collected.push(...page.data);
		if (!page.has_more || !page.next_cursor) {
			return { data: collected, has_more: false, next_cursor: null };
		}
		cursor = page.next_cursor;
	}
	// Fell off the safety cap — return what we have with ``has_more`` so
	// callers know it's truncated (unlikely to matter in practice).
	return { data: collected, has_more: true, next_cursor: cursor ?? null };
}

export function getVendorAuthCapabilities(
	vendorKey: string,
	registrationId?: string | null,
): Promise<VendorAuthCapabilities> {
	// Pin the read to a specific admin-registered OAuth app when the picker
	// tile carried one. Without the pin, the server uses the platform config
	// entry for the slug, else its single active registration (400 when
	// several registrations share the slug).
	const qs =
		registrationId != null
			? `?oauth_app_registration_id=${encodeURIComponent(registrationId)}`
			: '';
	return request(`/vendors/${encodeURIComponent(vendorKey)}/auth-capabilities${qs}`);
}

/** Wrap a generated-client failure as an {@link IntegrationsApiError}. */
function toIntegrationsError(err: unknown, fallback: string): IntegrationsApiError {
	const status =
		typeof (err as { status?: number })?.status === 'number'
			? (err as { status: number }).status
			: null;
	return new IntegrationsApiError((err as Error)?.message ?? fallback, status, err);
}

/**
 * Replace the full rule set on one direct agent ↔ credential binding
 * (`PUT /credentials/{cid}/agents/{aid}/permissions`) — the same endpoint the
 * agent's rules editor saves through, so a bind can grant access in one step
 * instead of leaving the binding blocked.
 */
export async function replaceBindingPermissions(
	agentId: string,
	credentialId: string,
	rules: PermissionRuleSchema[],
): Promise<PermissionRuleReadSchema[]> {
	try {
		const res = await CredentialsService.replaceAgentCredentialPermissions({
			credentialId,
			agentId,
			requestBody: rules,
		});
		return res.data;
	} catch (err) {
		throw toIntegrationsError(err, 'Failed to save the access rules.');
	}
}

/** One binding's saved rules (`GET /credentials/{cid}/agents/{aid}/permissions`). */
export async function listBindingPermissions(
	agentId: string,
	credentialId: string,
): Promise<PermissionRuleReadSchema[]> {
	try {
		const res = await CredentialsService.listAgentCredentialPermissions({
			credentialId,
			agentId,
		});
		return res.data;
	} catch (err) {
		throw toIntegrationsError(err, 'Failed to load the access rules.');
	}
}

/** One shared rule set with its ordered rules (`GET /permission-rule-sets/{id}`)
 * — what the broker evaluates for a binding that points at it. */
export async function getPermissionRuleSet(ruleSetId: string): Promise<RuleSetResponse> {
	try {
		return await PermissionRuleSetsService.getPermissionRuleSet({ ruleSetId });
	} catch (err) {
		throw toIntegrationsError(err, 'Failed to load the rule set.');
	}
}

/**
 * Thin adapter around the generated AgentsService so views/hooks in this
 * module never touch `@/shared/api` directly.
 */
export async function listAgentsForPicker(): Promise<AgentListResponse> {
	return AgentsService.listAgents({ limit: 100 });
}
