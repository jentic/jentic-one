/**
 * Settings MSW handlers + in-memory store — the admin OAuth-client registry
 * (`/admin/oauth-clients`), including the D7 approval lifecycle the
 * approval-queue tab drives (pending→approved/denied, denied→approved
 * recovery), the admin grants cross-view (`/admin/oauth-grants`) backing the
 * detail sheet's Grants panel, and the client-scoped audit slice
 * (`/audit?target_type=oauth_client`) backing its "Recent changes" panel.
 *
 * Shapes match the generated `OAuthClientResponse` / `OAuthGrantAdminResponse`
 * / `AuditResponse`; the state machine mirrors the backend: approve activates
 * the row, deny keeps it (inactive) so a later approve can reverse the
 * decision, decision/lifecycle verbs append audit rows (deny with the
 * operator's reason), and `active_grant_count` is computed from the grants
 * store. Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

interface OAuthClientRow {
	id: string;
	client_id: string;
	name: string;
	description: string | null;
	redirect_uris: string[];
	allowed_scopes: string[] | null;
	token_endpoint_auth_method: string;
	consent_model: string;
	require_consent: boolean;
	active: boolean;
	registration_source: string;
	software_id: string | null;
	approval_status: 'pending' | 'approved' | 'denied';
	active_grant_count: number;
	created_at: string;
	created_by: string | null;
	updated_at: string | null;
}

/** One consent→agent grant in the admin cross-view (`OAuthGrantAdminResponse`). */
interface OAuthGrantAdminRow {
	id: string;
	oauth_client_id: string;
	client_name: string | null;
	client_origin: string | null;
	agent_id: string;
	user_id: string;
	scopes: string[];
	status: 'active' | 'revoked';
	can_revoke: boolean;
	created_at: string;
	revoked_at: string | null;
	last_used_at: string | null;
}

/** One client-scoped audit row (`AuditResponse`, target_type=oauth_client). */
interface OAuthClientAuditRow {
	id: string;
	action: string;
	actor_id: string | null;
	actor_type: string;
	target_type: 'oauth_client';
	target_id: string;
	reason: string | null;
	occurred_at: string;
}

const now = (offsetMin = 0) => new Date(Date.now() + offsetMin * 60_000).toISOString();

function seedClient(
	over: Partial<OAuthClientRow> & Pick<OAuthClientRow, 'id' | 'client_id' | 'name'>,
): OAuthClientRow {
	return {
		description: null,
		redirect_uris: ['https://app.example.com/callback'],
		allowed_scopes: ['apis:read'],
		token_endpoint_auth_method: 'client_secret_basic',
		consent_model: 'user',
		require_consent: true,
		active: true,
		registration_source: 'admin',
		software_id: null,
		approval_status: 'approved',
		active_grant_count: 0,
		created_at: now(-120),
		created_by: 'usr_admin_1',
		updated_at: null,
		...over,
	};
}

/** Mutable per-session store. Reset between tests via `resetSettingsStore()`. */
let oauthClients: OAuthClientRow[] = [];
let oauthGrants: OAuthGrantAdminRow[] = [];
let oauthClientAudit: OAuthClientAuditRow[] = [];
let auditSeq = 0;

function appendAudit(row: OAuthClientRow, action: string, reason: string | null = null): void {
	oauthClientAudit.unshift({
		id: `aud_oac_${auditSeq++}`,
		action,
		actor_id: 'usr_admin_1',
		actor_type: 'user',
		target_type: 'oauth_client',
		target_id: row.id,
		reason,
		occurred_at: now(),
	});
}

export function resetSettingsStore(): void {
	oauthClients = [
		// An admin-registered confidential client, live, with grants attached
		// (the grants store below carries its two active rows).
		seedClient({
			id: 'oac_admin_1',
			client_id: 'oc_dashboard',
			name: 'Internal Dashboard',
			description: 'Company metrics dashboard.',
		}),
		// A DCR-registered PUBLIC client awaiting the D7 approval decision:
		// inactive by construction until approved, no secret (PKCE-only). The
		// second redirect URI is a NON-SPECIAL scheme (WHATWG opaque origin)
		// — exactly the native/MCP client class — pinning the queue headline's
		// scheme://host derivation instead of a literal "null".
		seedClient({
			id: 'oac_pending_1',
			client_id: 'oc_cursor_ide',
			name: 'Cursor',
			redirect_uris: [
				'http://localhost:33418/callback',
				'cursor://anysphere.cursor-mcp/oauth/callback',
			],
			token_endpoint_auth_method: 'none',
			consent_model: 'agent',
			registration_source: 'dcr',
			software_id: 'com.cursor.ide',
			approval_status: 'pending',
			active: false,
			created_at: now(-10),
			created_by: null,
		}),
		// A previously denied DCR client — the denied→approved recovery path.
		// Deliberately noisy (many scopes, several redirect URIs) so the
		// queue card's "+N more" scope expander and the redirect-URI
		// disclosure have a real target.
		seedClient({
			id: 'oac_denied_1',
			client_id: 'oc_sketchy_tool',
			name: 'Sketchy Tool',
			redirect_uris: [
				'https://sketchy.example.com/cb',
				'https://sketchy.example.com/cb2',
				'https://alt.sketchy.example.com/cb',
			],
			allowed_scopes: [
				'apis:read',
				'apis:write',
				'agents:read',
				'agents:write',
				'credentials:read',
				'audit:read',
			],
			token_endpoint_auth_method: 'none',
			registration_source: 'dcr',
			approval_status: 'denied',
			active: false,
			created_at: now(-30),
			created_by: null,
		}),
		// The #1312 "zombie": approved but deactivated — matches neither queue
		// filter, only surfaced by the roster's Inactive status segment.
		seedClient({
			id: 'oac_zombie_1',
			client_id: 'oc_legacy_app',
			name: 'Legacy App',
			approval_status: 'approved',
			active: false,
			created_at: now(-240),
			updated_at: now(-60),
		}),
	];
	oauthGrants = [
		// Active grant the mock admin CAN revoke.
		{
			id: 'ocg_1',
			oauth_client_id: 'oc_dashboard',
			client_name: 'Internal Dashboard',
			client_origin: 'https://app.example.com',
			agent_id: 'invoice-bot',
			user_id: 'usr_admin_1',
			scopes: ['apis:read'],
			status: 'active',
			can_revoke: true,
			created_at: now(-90),
			revoked_at: null,
			last_used_at: now(-5),
		},
		// Active grant the CALLER may not revoke (G10: list predicate wider
		// than revoke predicate) — the UI must disable, not offer a 403.
		{
			id: 'ocg_2',
			oauth_client_id: 'oc_dashboard',
			client_name: 'Internal Dashboard',
			client_origin: 'https://app.example.com',
			agent_id: 'support-triage',
			user_id: 'usr_other_1',
			scopes: ['apis:read', 'executions:read'],
			status: 'active',
			can_revoke: false,
			created_at: now(-60),
			revoked_at: null,
			last_used_at: null,
		},
		// Revoked history row — reachable via the Revoked/All filters.
		{
			id: 'ocg_3',
			oauth_client_id: 'oc_dashboard',
			client_name: 'Internal Dashboard',
			client_origin: 'https://app.example.com',
			agent_id: 'invoice-bot',
			user_id: 'usr_admin_1',
			scopes: ['apis:read'],
			status: 'revoked',
			can_revoke: false,
			created_at: now(-200),
			revoked_at: now(-100),
			last_used_at: now(-150),
		},
	];
	// Decision history for the seeded rows: the denied client carries its
	// deny REASON (the queue captured it; the detail sheet surfaces it).
	oauthClientAudit = [
		{
			id: 'aud_oac_seed_1',
			action: 'oauth_client.deny',
			actor_id: 'usr_admin_1',
			actor_type: 'user',
			target_type: 'oauth_client',
			target_id: 'oac_denied_1',
			reason: 'unknown redirect URIs',
			occurred_at: now(-25),
		},
		{
			id: 'aud_oac_seed_2',
			action: 'oauth_client.register',
			actor_id: null,
			actor_type: 'system',
			target_type: 'oauth_client',
			target_id: 'oac_admin_1',
			reason: null,
			occurred_at: now(-120),
		},
	];
	auditSeq = 0;
}

resetSettingsStore();

function genId(prefix: string): string {
	return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

/** `active_grant_count` is computed on the read path, like the backend. */
function withGrantCount(row: OAuthClientRow): OAuthClientRow {
	return {
		...row,
		active_grant_count: oauthGrants.filter(
			(g) => g.oauth_client_id === row.client_id && g.status === 'active',
		).length,
	};
}

export const settingsHandlers = [
	http.get('/admin/oauth-clients', ({ request }) => {
		const url = new URL(request.url);
		const approvalStatus = url.searchParams.get('approval_status');
		const includeInactive =
			url.searchParams.get('include_inactive') === 'true' ||
			// Mirrors the backend: a pending/denied filter implies include_inactive
			// (those rows are inactive by construction — D7).
			approvalStatus === 'pending' ||
			approvalStatus === 'denied';
		const rows = oauthClients
			.filter(
				(c) =>
					(includeInactive || c.active) &&
					(!approvalStatus || c.approval_status === approvalStatus),
			)
			.map(withGrantCount);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
	http.post('/admin/oauth-clients', async ({ request }) => {
		const body = (await request.json()) as Partial<OAuthClientRow> & { name: string };
		const authMethod = body.token_endpoint_auth_method ?? 'client_secret_basic';
		const row = seedClient({
			id: genId('oac'),
			client_id: genId('oc'),
			name: body.name,
			description: body.description ?? null,
			redirect_uris: body.redirect_uris ?? [],
			allowed_scopes: body.allowed_scopes ?? null,
			token_endpoint_auth_method: authMethod,
			consent_model: body.consent_model ?? 'user',
			require_consent: body.require_consent ?? true,
			created_at: now(),
		});
		oauthClients.push(row);
		appendAudit(row, 'oauth_client.create');
		// Public (PKCE-only) clients get no secret — mirror the backend.
		const secret = authMethod === 'none' ? null : 'ocs_mock_secret_once';
		return HttpResponse.json({ ...row, client_secret: secret }, { status: 201 });
	}),
	http.get('/admin/oauth-clients/:id', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		return row
			? HttpResponse.json(withGrantCount(row))
			: new HttpResponse(null, { status: 404 });
	}),
	http.patch('/admin/oauth-clients/:id', async ({ params, request }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as Partial<OAuthClientRow>;
		// Mirrors the backend gate: only approved clients may be (re)activated.
		if (body.active === true && row.approval_status !== 'approved') {
			return new HttpResponse(null, { status: 409 });
		}
		// allowed_scopes is TRI-STATE on PATCH, mirroring oauth_client_service:
		// null/omitted = NO CHANGE, ['*'] = reset to unrestricted (stored
		// null), any other array = restrict to it ([] = OIDC-only). A plain
		// Object.assign would treat null as "clear" and diverge.
		const { allowed_scopes: scopeUpdate, ...rest } = body;
		Object.assign(row, rest, { updated_at: now() });
		if (scopeUpdate != null) {
			row.allowed_scopes = scopeUpdate.includes('*') ? null : scopeUpdate;
		}
		return HttpResponse.json(withGrantCount(row));
	}),
	http.delete('/admin/oauth-clients/:id', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.active = false;
		appendAudit(row, 'oauth_client.deactivate');
		return new HttpResponse(null, { status: 204 });
	}),
	http.post('/admin/oauth-clients/:id/rotate-secret', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.token_endpoint_auth_method === 'none') {
			return new HttpResponse(null, { status: 409 });
		}
		appendAudit(row, 'oauth_client.rotate_secret');
		return HttpResponse.json({ client_secret: 'ocs_mock_rotated_once' });
	}),
	// D7 approval verbs: approve activates; deny keeps the row for recovery.
	http.post('/admin/oauth-clients/:id\\:approve', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.approval_status = 'approved';
		row.active = true;
		row.updated_at = now();
		appendAudit(row, 'oauth_client.approve');
		return HttpResponse.json(withGrantCount(row));
	}),
	http.post('/admin/oauth-clients/:id\\:deny', async ({ params, request }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json().catch(() => null)) as { reason?: string } | null;
		row.approval_status = 'denied';
		row.active = false;
		row.updated_at = now();
		appendAudit(row, 'oauth_client.deny', body?.reason ?? null);
		return HttpResponse.json(withGrantCount(row));
	}),
	// ---- Admin grants cross-view: the detail sheet's Grants panel ----
	http.get('/admin/oauth-grants', ({ request }) => {
		const url = new URL(request.url);
		const clientId = url.searchParams.get('client_id');
		const status = url.searchParams.get('status');
		const rows = oauthGrants.filter(
			(g) =>
				(!clientId || g.oauth_client_id === clientId) && (!status || g.status === status),
		);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
	// The §4.6 grant kill switch. The agents module registers the same path
	// for ITS store and falls through (returns undefined) for unknown grant
	// ids, so this handler answers for the settings-store grants.
	http.post('/oauth-grants/:id\\:revoke', ({ params }) => {
		const grant = oauthGrants.find((g) => g.id === params.id);
		if (!grant) return undefined;
		// Idempotent, like the backend: re-revoking is a 204 no-op.
		if (grant.status === 'active') {
			grant.status = 'revoked';
			grant.revoked_at = now();
			grant.can_revoke = false;
		}
		return new HttpResponse(null, { status: 204 });
	}),
	// Client-scoped audit slice ("Recent changes"): answer only for
	// oauth_client targets, else fall through (the agents module owns actor
	// targets; the monitor module owns the org-wide fixture).
	http.get('/audit', ({ request }) => {
		const url = new URL(request.url);
		if (url.searchParams.get('target_type') !== 'oauth_client') return undefined;
		const targetId = url.searchParams.get('target_id');
		const rows = oauthClientAudit.filter((a) => !targetId || a.target_id === targetId);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
];
