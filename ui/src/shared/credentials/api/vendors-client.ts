/**
 * Integrations repository tier — the ONLY place in the module that talks
 * HTTP. Views and hooks import this module, not `fetch` directly.
 *
 * Every call goes through the generated services in `@/shared/api`; a
 * failure is rethrown as an {@link IntegrationsApiError} carrying the
 * problem's `type` slug and extension members. The one hand-built request is
 * {@link cancelConnectSessionOnUnload}, whose `keepalive` fetch the
 * generated client can't express.
 */

import {
	AgentsService,
	ApiError,
	ApiOperationsService,
	CredentialsService,
	IntegrationsService,
	PermissionRuleSetsService,
	VendorsService,
	getToken,
	problemDetailText,
	type AgentListResponse,
	type ConnectSessionSummaryResponse,
	type IntegrationsConnectRequest,
	type PermissionRuleReadSchema,
	type PermissionRuleSchema,
	type RuleSetResponse,
} from '@/shared/api';
import type {
	ConfirmSessionBody,
	ConfirmSessionResponse,
	ConnectRequest,
	ConnectResponse,
	ReviewSession,
	StatusResponse,
	VendorAuthCapabilities,
	VendorListResponse,
	VendorOperation,
	VendorOperationsPage,
} from '@/shared/credentials/api/connect-types';

export type { VendorOperation, VendorOperationsPage };

export class IntegrationsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;
	/**
	 * The problem's ``type`` slug (e.g. ``review_stale``) — what callers branch
	 * on. Null for a network error or a body that isn't problem+json.
	 */
	readonly code: string | null;
	/**
	 * The problem body's extension members (e.g. ``missing_scopes``). Server
	 * data only: the connect-session routes never echo request input.
	 */
	readonly extensions: Readonly<Record<string, unknown>>;

	constructor(
		message: string,
		status: number | null,
		cause?: unknown,
		code: string | null = null,
		extensions: Readonly<Record<string, unknown>> = {},
	) {
		super(message);
		this.name = 'IntegrationsApiError';
		this.status = status;
		this.cause = cause;
		this.code = code;
		this.extensions = extensions;
	}
}

/** Problem+json members every problem carries; the rest are extensions. */
const PROBLEM_MEMBERS = new Set(['type', 'title', 'detail', 'status', 'instance']);

/**
 * A generated-client failure as an {@link IntegrationsApiError}. An HTTP error
 * keeps the RFC 9457 body's ``detail`` (else the status text), ``type`` slug
 * and extension members; anything else is a network error with no status.
 */
function toIntegrationsError(err: unknown, fallback: string): IntegrationsApiError {
	if (err instanceof IntegrationsApiError) return err;
	if (!(err instanceof ApiError)) {
		return new IntegrationsApiError(
			err instanceof Error && err.message ? err.message : fallback,
			null,
			err,
		);
	}
	const body: unknown = err.body;
	let code: string | null = null;
	const extensions: Record<string, unknown> = {};
	if (body && typeof body === 'object') {
		const record = body as Record<string, unknown>;
		if (typeof record.type === 'string' && record.type) code = record.type;
		for (const [k, v] of Object.entries(record)) {
			if (!PROBLEM_MEMBERS.has(k)) extensions[k] = v;
		}
	}
	return new IntegrationsApiError(
		problemDetailText(body) ?? (err.statusText || `HTTP ${err.status}`),
		err.status,
		err,
		code,
		extensions,
	);
}

/** Await a generated call, rethrowing its failure as an {@link IntegrationsApiError}. */
async function call<T>(promise: PromiseLike<T>, fallback: string): Promise<T> {
	try {
		return await promise;
	} catch (err) {
		throw toIntegrationsError(err, fallback);
	}
}

/**
 * The view types widen the generated enums to their string values; the wire
 * shape is the same, so a body crosses into the generated client as-is.
 */
function wire<T>(body: unknown): T {
	return body as T;
}

/** The generated `:confirm` body union. */
type ConfirmSessionWireBody = Parameters<
	typeof IntegrationsService.confirmConnectSession
>[0]['requestBody'];

export function startIntegrationConnect(body: ConnectRequest): Promise<ConnectResponse> {
	return call(
		IntegrationsService.integrationsConnect({
			requestBody: wire<IntegrationsConnectRequest>(body),
		}),
		'Failed to start the connect session.',
	);
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
	await call(
		AgentsService.bindAgentCredential({
			agentId,
			requestBody: { credential_id: credentialId },
		}),
		'Failed to bind the credential.',
	);
}

/**
 * The ``poll_token`` capability for a session-scoped route, when the caller
 * holds one. Without it the backend authorises the target agent's owner
 * (holding ``credentials:write`` and ``agents:write``) or ``org:admin``
 * instead; everyone else gets the same 403 as a wrong token.
 */
function pollTokenParam(pollToken?: string): string | undefined {
	return pollToken || undefined;
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
	return call(
		IntegrationsService.getConnectSession({
			sessionId,
			pollToken: pollTokenParam(pollToken),
		}),
		'Failed to load the connect session.',
	);
}

/**
 * Confirm the session: OAuth scopes + rules (kicks off the vendor flow), a
 * typed secret, the approver's own OAuth client, or an existing credential —
 * ``body.kind`` picks the variant. Gated like the review read (403 on
 * mismatch, missing session, or a token-less caller who is not the owner /
 * admin) and shares the ``:connect`` rate bucket (429 possible). A secret in
 * the body is never echoed back, including in an error.
 */
export function confirmConnectSession(
	sessionId: string,
	pollToken: string | undefined,
	body: ConfirmSessionBody,
): Promise<ConfirmSessionResponse> {
	return call(
		IntegrationsService.confirmConnectSession({
			sessionId,
			pollToken: pollTokenParam(pollToken),
			requestBody: wire<ConfirmSessionWireBody>(body),
		}),
		'Failed to confirm the connect session.',
	) as Promise<ConfirmSessionResponse>;
}

export function pollConnectSessionStatus(
	sessionId: string,
	pollToken?: string,
): Promise<StatusResponse> {
	return call(
		IntegrationsService.pollConnectSessionStatus({
			sessionId,
			pollToken: pollTokenParam(pollToken),
		}),
		'Failed to load the connect session status.',
	);
}

/**
 * Cancel an in-flight connect session. Idempotent — the backend
 * ``:cancel`` route returns 204 even if the session is already gone,
 * so we can fire this from unmount cleanup without needing to check
 * whether the flow already finished.
 */
export function cancelConnectSession(sessionId: string, pollToken?: string): Promise<void> {
	return call(
		IntegrationsService.cancelConnectSession({
			sessionId,
			pollToken: pollTokenParam(pollToken),
		}),
		'Failed to cancel the connect session.',
	);
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
	const path = `/connect-sessions/${encodeURIComponent(sessionId)}:cancel`;
	const url = pollToken ? `${path}?poll_token=${encodeURIComponent(pollToken)}` : path;
	void fetch(url, {
		method: 'POST',
		headers,
		keepalive: true,
	}).catch(() => {});
	return true;
}

/**
 * Reject an agent's request — the approve dialog's explicit Reject. The agent
 * sees ``failed`` with ``error_code: rejected`` and may not ask again for the
 * same target during the rejection cooldown. Owner (``credentials:write`` and
 * ``agents:write``) or ``org:admin`` only; there is no poll-token path, so the
 * route takes none. Closing the dialog without rejecting sends nothing.
 */
export function rejectConnectSession(sessionId: string): Promise<void> {
	return call(
		IntegrationsService.rejectConnectSession({ sessionId }),
		'Failed to reject the connect request.',
	);
}

/**
 * Session states in which an agent is still waiting on a human —
 * ``awaiting_app`` is an OAuth API with no app to connect through yet.
 */
const OPEN_CONNECT_STATES = ['created', 'awaiting_app', 'polling'] as const;

/** Upper bound per state; open sessions expire fast, so one page holds them. */
const OPEN_CONNECT_PAGE_LIMIT = 200;

/**
 * The connect sessions an agent opened and is still waiting on a human for
 * (``created``, ``awaiting_app`` or ``polling``), oldest first. The list is scoped server-side:
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
	return call(VendorsService.listVendors(), 'Failed to load vendors.');
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
	try {
		const page = await call(
			ApiOperationsService.listApiOperations({
				vendor,
				name,
				version,
				cursor: opts.cursor || undefined,
				limit: opts.limit,
			}),
			'Failed to load the API operations.',
		);
		return { data: page.data, has_more: page.has_more, next_cursor: page.next_cursor ?? null };
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
	return call(
		VendorsService.getAuthCapabilities({
			vendorKey,
			oauthAppRegistrationId: registrationId ?? undefined,
		}),
		'Failed to load the vendor.',
	);
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

/** The server's largest `GET /agents` page. */
const AGENT_PAGE_LIMIT = 200;

/** Cap on pages fetched for the picker, so a runaway cursor can't hang the SPA. */
const AGENT_PICKER_MAX_PAGES = 50;

/**
 * Every agent the caller can see, for the agent pickers: follows
 * `next_cursor` until the last page (or the page cap, which leaves
 * `has_more` set).
 */
export async function listAgentsForPicker(): Promise<AgentListResponse> {
	const data: AgentListResponse['data'] = [];
	let cursor: string | null = null;
	for (let i = 0; i < AGENT_PICKER_MAX_PAGES; i++) {
		const page: AgentListResponse = await call(
			AgentsService.listAgents({ cursor, limit: AGENT_PAGE_LIMIT }),
			'Failed to load agents.',
		);
		data.push(...page.data);
		if (!page.has_more || !page.next_cursor) {
			return { data, has_more: false, next_cursor: null };
		}
		cursor = page.next_cursor;
	}
	return { data, has_more: true, next_cursor: cursor };
}
