/**
 * MSW handlers + in-memory store for the OAuth app registrations admin
 * endpoints (`/oauth-app-registrations`). Shapes match the generated
 * `OAuthAppRegistrationResponse`; state machine mirrors the backend:
 * PATCH is partial, `client_id` is immutable, delete refuses (409) while
 * `dependent_credential_count > 0`, and reads never surface the client
 * secret (only `has_client_secret` + `secret_last_rotated_at`).
 *
 * Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

type FlowKind = 'authorization_code' | 'device_authorization';

interface RegistrationRow {
	id: string;
	name: string;
	api_vendor: string;
	flow_kind: FlowKind;
	client_id: string;
	// The secret stays in the mock store but is NEVER returned by any read
	// path — mirroring the backend.
	_client_secret: string | null;
	authorize_url: string | null;
	token_url: string | null;
	authorization_endpoint: string | null;
	token_endpoint: string | null;
	default_scopes: string[] | null;
	is_active: boolean;
	secret_last_rotated_at: string | null;
	dependent_credential_count: number;
	created_at: string;
	updated_at: string;
	created_by: string | null;
}

const now = (offsetMin = 0): string => new Date(Date.now() + offsetMin * 60_000).toISOString();

function genId(prefix: string): string {
	return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

let registrations: RegistrationRow[] = [];

export function resetOAuthAppRegistrationsStore(): void {
	registrations = [
		{
			id: 'oar_github_prod',
			name: 'GitHub production app',
			api_vendor: 'github',
			flow_kind: 'authorization_code',
			client_id: 'gh_client_prod',
			_client_secret: 'secret_masked_prod',
			authorize_url: 'https://github.com/login/oauth/authorize',
			token_url: 'https://github.com/login/oauth/access_token',
			authorization_endpoint: null,
			token_endpoint: null,
			default_scopes: ['read:user', 'repo'],
			is_active: true,
			secret_last_rotated_at: now(-1440),
			dependent_credential_count: 2,
			created_at: now(-4320),
			updated_at: now(-1440),
			created_by: 'usr_admin_1',
		},
		{
			id: 'oar_github_device',
			name: 'GitHub CLI (device flow)',
			api_vendor: 'github',
			flow_kind: 'device_authorization',
			client_id: 'gh_client_device',
			_client_secret: null,
			authorize_url: null,
			token_url: null,
			authorization_endpoint: 'https://github.com/login/device/code',
			token_endpoint: 'https://github.com/login/oauth/access_token',
			default_scopes: ['read:user'],
			is_active: true,
			secret_last_rotated_at: null,
			dependent_credential_count: 0,
			created_at: now(-2880),
			updated_at: now(-2880),
			created_by: 'usr_admin_1',
		},
		{
			id: 'oar_slack_inactive',
			name: 'Slack (paused)',
			api_vendor: 'slack',
			flow_kind: 'authorization_code',
			client_id: 'slack_client_paused',
			_client_secret: 'secret_masked_slack',
			authorize_url: 'https://slack.com/oauth/v2/authorize',
			token_url: 'https://slack.com/api/oauth.v2.access',
			authorization_endpoint: null,
			token_endpoint: null,
			default_scopes: null,
			is_active: false,
			secret_last_rotated_at: now(-10080),
			dependent_credential_count: 0,
			created_at: now(-14400),
			updated_at: now(-1440),
			created_by: 'usr_admin_1',
		},
	];
}

resetOAuthAppRegistrationsStore();

/** Strip the sensitive `_client_secret` before serialising to a wire response. */
function toWire(row: RegistrationRow): Omit<RegistrationRow, '_client_secret'> & {
	has_client_secret: boolean;
} {
	const { _client_secret: secret, ...rest } = row;
	return {
		...rest,
		has_client_secret: secret != null,
	};
}

export const oauthAppRegistrationsHandlers = [
	http.get('/oauth-app-registrations', ({ request }) => {
		const url = new URL(request.url);
		const apiVendor = url.searchParams.get('api_vendor');
		const includeInactive = url.searchParams.get('include_inactive') === 'true';
		const flowKind = url.searchParams.get('flow_kind') as FlowKind | null;
		const rows = registrations
			.filter((r) => includeInactive || r.is_active)
			.filter((r) => !apiVendor || r.api_vendor === apiVendor)
			.filter((r) => !flowKind || r.flow_kind === flowKind)
			.map(toWire);
		return HttpResponse.json({ data: rows });
	}),
	http.post('/oauth-app-registrations', async ({ request }) => {
		const body = (await request.json()) as {
			flow_kind: FlowKind;
			name: string;
			api_vendor: string;
			client_id: string;
			client_secret?: string;
			authorize_url?: string;
			token_url?: string;
			authorization_endpoint?: string;
			token_endpoint?: string;
			default_scopes?: string[] | null;
		};
		const row: RegistrationRow = {
			id: genId('oar'),
			name: body.name,
			api_vendor: body.api_vendor,
			flow_kind: body.flow_kind,
			client_id: body.client_id,
			_client_secret:
				body.flow_kind === 'authorization_code' ? (body.client_secret ?? null) : null,
			authorize_url: body.authorize_url ?? null,
			token_url: body.token_url ?? null,
			authorization_endpoint: body.authorization_endpoint ?? null,
			token_endpoint: body.token_endpoint ?? null,
			default_scopes: body.default_scopes ?? null,
			is_active: true,
			secret_last_rotated_at: body.flow_kind === 'authorization_code' ? now() : null,
			dependent_credential_count: 0,
			created_at: now(),
			updated_at: now(),
			created_by: 'usr_admin_1',
		};
		registrations.push(row);
		return HttpResponse.json(toWire(row), { status: 201 });
	}),
	http.get('/oauth-app-registrations/:id', ({ params }) => {
		const row = registrations.find((r) => r.id === params.id);
		return row ? HttpResponse.json(toWire(row)) : new HttpResponse(null, { status: 404 });
	}),
	http.patch('/oauth-app-registrations/:id', async ({ params, request }) => {
		const row = registrations.find((r) => r.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as Partial<
			Pick<
				RegistrationRow,
				| 'name'
				| 'authorize_url'
				| 'token_url'
				| 'authorization_endpoint'
				| 'token_endpoint'
				| 'default_scopes'
				| 'is_active'
			>
		>;
		Object.assign(row, body, { updated_at: now() });
		return HttpResponse.json(toWire(row));
	}),
	http.post('/oauth-app-registrations/:id\\:rotate-secret', async ({ params, request }) => {
		const row = registrations.find((r) => r.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.flow_kind !== 'authorization_code') {
			return HttpResponse.json(
				{ error: 'rotate_not_supported', detail: 'Device flow has no client secret.' },
				{ status: 400 },
			);
		}
		const body = (await request.json()) as { client_secret: string };
		row._client_secret = body.client_secret;
		row.secret_last_rotated_at = now();
		row.updated_at = now();
		return HttpResponse.json(toWire(row));
	}),
	http.delete('/oauth-app-registrations/:id', ({ params }) => {
		const row = registrations.find((r) => r.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.dependent_credential_count > 0) {
			return HttpResponse.json(
				{
					error: 'oauth_app_registration_in_use',
					detail: `Still referenced by ${row.dependent_credential_count} credential(s).`,
				},
				{ status: 409 },
			);
		}
		registrations = registrations.filter((r) => r.id !== row.id);
		return new HttpResponse(null, { status: 204 });
	}),
];
