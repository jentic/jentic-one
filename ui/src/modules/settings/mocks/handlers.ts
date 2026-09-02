/**
 * Settings MSW handlers + in-memory store — the admin OAuth-client registry
 * (`/admin/oauth-clients`), including the phase-3a D7 approval lifecycle the
 * approval-queue tab drives (pending→approved/denied, denied→approved
 * recovery) and the §4.8 per-client active-grant count.
 *
 * Shapes match the generated `OAuthClientResponse`; the state machine mirrors
 * the backend: approve activates the row, deny keeps it (inactive) so a later
 * approve can reverse the decision. Registered additively in
 * src/mocks/handlers.ts.
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

export function resetSettingsStore(): void {
	oauthClients = [
		// An admin-registered confidential client, live, with grants attached.
		seedClient({
			id: 'oac_admin_1',
			client_id: 'oc_dashboard',
			name: 'Internal Dashboard',
			description: 'Company metrics dashboard.',
			active_grant_count: 2,
		}),
		// A DCR-registered PUBLIC client awaiting the D7 approval decision:
		// inactive by construction until approved, no secret (PKCE-only).
		seedClient({
			id: 'oac_pending_1',
			client_id: 'oc_cursor_ide',
			name: 'Cursor',
			redirect_uris: ['http://localhost:33418/callback'],
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
		seedClient({
			id: 'oac_denied_1',
			client_id: 'oc_sketchy_tool',
			name: 'Sketchy Tool',
			token_endpoint_auth_method: 'none',
			registration_source: 'dcr',
			approval_status: 'denied',
			active: false,
			created_at: now(-30),
			created_by: null,
		}),
	];
}

resetSettingsStore();

function genId(prefix: string): string {
	return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
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
		const rows = oauthClients.filter(
			(c) =>
				(includeInactive || c.active) &&
				(!approvalStatus || c.approval_status === approvalStatus),
		);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
	http.post('/admin/oauth-clients', async ({ request }) => {
		const body = (await request.json()) as Partial<OAuthClientRow> & { name: string };
		const row = seedClient({
			id: genId('oac'),
			client_id: genId('oc'),
			name: body.name,
			description: body.description ?? null,
			redirect_uris: body.redirect_uris ?? [],
			allowed_scopes: body.allowed_scopes ?? null,
			token_endpoint_auth_method: body.token_endpoint_auth_method ?? 'client_secret_basic',
			require_consent: body.require_consent ?? true,
			created_at: now(),
		});
		oauthClients.push(row);
		return HttpResponse.json(
			{ ...row, client_secret: 'ocs_mock_secret_once' },
			{ status: 201 },
		);
	}),
	http.get('/admin/oauth-clients/:id', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		return row ? HttpResponse.json(row) : new HttpResponse(null, { status: 404 });
	}),
	http.patch('/admin/oauth-clients/:id', async ({ params, request }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as Partial<OAuthClientRow>;
		// Mirrors the backend gate: only approved clients may be (re)activated.
		if (body.active === true && row.approval_status !== 'approved') {
			return new HttpResponse(null, { status: 409 });
		}
		Object.assign(row, body, { updated_at: now() });
		return HttpResponse.json(row);
	}),
	http.delete('/admin/oauth-clients/:id', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.active = false;
		return new HttpResponse(null, { status: 204 });
	}),
	http.post('/admin/oauth-clients/:id/rotate-secret', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.token_endpoint_auth_method === 'none') {
			return new HttpResponse(null, { status: 409 });
		}
		return HttpResponse.json({ client_secret: 'ocs_mock_rotated_once' });
	}),
	// D7 approval verbs: approve activates; deny keeps the row for recovery.
	http.post('/admin/oauth-clients/:id\\:approve', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.approval_status = 'approved';
		row.active = true;
		row.updated_at = now();
		return HttpResponse.json(row);
	}),
	http.post('/admin/oauth-clients/:id\\:deny', ({ params }) => {
		const row = oauthClients.find((c) => c.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.approval_status = 'denied';
		row.active = false;
		row.updated_at = now();
		return HttpResponse.json(row);
	}),
];
