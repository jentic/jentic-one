import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	IntegrationsApiError,
	cancelConnectSession,
	confirmConnectSession,
	getConnectSession,
	getVendorAuthCapabilities,
	listAgentsForPicker,
	listAllVendorOperations,
	listOpenConnectRequests,
	pollConnectSessionStatus,
	rejectConnectSession,
	startIntegrationConnect,
} from '@/shared/credentials/api/vendors-client';

/**
 * The connect-session repository tier calls the generated `IntegrationsService`;
 * these pin the wire contract it relies on (query capability, body, and the
 * problem+json → `IntegrationsApiError` mapping callers branch on).
 */
describe('vendors-client over the generated client', () => {
	it('sends the poll token as a query parameter, and none when absent', async () => {
		const seen: (string | null)[] = [];
		worker.use(
			http.get('/connect-sessions/:id/status', ({ request }) => {
				seen.push(new URL(request.url).searchParams.get('poll_token'));
				return HttpResponse.json({ status: 'polling' });
			}),
		);

		await expect(pollConnectSessionStatus('cs_1', 'tok_1')).resolves.toMatchObject({
			status: 'polling',
		});
		await pollConnectSessionStatus('cs_1');

		expect(seen).toEqual(['tok_1', null]);
	});

	it('posts the confirm body and returns the discriminated result', async () => {
		let body: unknown = null;
		worker.use(
			http.post('/connect-sessions/:id', async ({ request, params }) => {
				expect(params.id).toBe('cs_1:confirm');
				body = await request.json();
				return HttpResponse.json({ kind: 'connected', credential_id: 'cred_1' });
			}),
		);

		const res = await confirmConnectSession('cs_1', undefined, {
			kind: 'api_key',
			key: 'sk_test', // pragma: allowlist secret
			permission_rules: [{ effect: 'allow', methods: ['GET'] }],
			expected_agent_id: null,
			digest: 'd1',
		});

		expect(res).toEqual({ kind: 'connected', credential_id: 'cred_1' });
		expect(body).toMatchObject({ kind: 'api_key', digest: 'd1' });
	});

	it('maps a problem+json error to its type slug, detail and extensions', async () => {
		worker.use(
			http.get('/connect-sessions/:id', () =>
				HttpResponse.json(
					{
						type: 'review_stale',
						title: 'Conflict',
						status: 409,
						detail: 'The review is stale.',
						missing_scopes: ['repo'],
					},
					{ status: 409, headers: { 'Content-Type': 'application/problem+json' } },
				),
			),
		);

		const err = await getConnectSession('cs_1', 'tok_1').catch((e: unknown) => e);

		expect(err).toBeInstanceOf(IntegrationsApiError);
		const apiErr = err as IntegrationsApiError;
		expect(apiErr.status).toBe(409);
		expect(apiErr.code).toBe('review_stale');
		expect(apiErr.message).toBe('The review is stale.');
		expect(apiErr.extensions).toEqual({ missing_scopes: ['repo'] });
	});

	it('maps a network failure to an error with no status', async () => {
		worker.use(http.get('/connect-sessions/:id', () => HttpResponse.error()));

		const err = await getConnectSession('cs_1').catch((e: unknown) => e);

		expect(err).toBeInstanceOf(IntegrationsApiError);
		expect((err as IntegrationsApiError).status).toBeNull();
		expect((err as IntegrationsApiError).code).toBeNull();
		// The fetch TypeError's own text, not the call's fallback message.
		expect((err as IntegrationsApiError).message).toBe('Failed to fetch');
	});

	it('posts the connect body and returns the 201 session', async () => {
		let body: unknown = null;
		worker.use(
			http.post('/integrations\\:connect', async ({ request }) => {
				body = await request.json();
				return HttpResponse.json(
					{
						session_id: 'cs_9',
						approval_url: '/connect?approve=cs_9',
						poll_token: 'tok_9',
						resolved_flow: 'manual_api_key',
					},
					{ status: 201 },
				);
			}),
		);

		const res = await startIntegrationConnect({ vendor: 'github.com', agent_id: 'agnt_1' });

		expect(res).toMatchObject({ session_id: 'cs_9', poll_token: 'tok_9' });
		expect(body).toEqual({ vendor: 'github.com', agent_id: 'agnt_1' });
	});

	it('resolves cancel and reject on 204 with no body', async () => {
		const seen: string[] = [];
		worker.use(
			http.post('/connect-sessions/:id\\:cancel', ({ params, request }) => {
				const token = new URL(request.url).searchParams.get('poll_token');
				seen.push(`cancel ${String(params.id)} ${token}`);
				return new HttpResponse(null, { status: 204 });
			}),
			http.post('/connect-sessions/:id\\:reject', ({ params }) => {
				seen.push(`reject ${String(params.id)}`);
				return new HttpResponse(null, { status: 204 });
			}),
		);

		await expect(cancelConnectSession('cs_1', 'tok_1')).resolves.toBeUndefined();
		await expect(rejectConnectSession('cs_2')).resolves.toBeUndefined();

		expect(seen).toEqual(['cancel cs_1 tok_1', 'reject cs_2']);
	});

	it('maps a cancel or reject problem body to IntegrationsApiError', async () => {
		const forbidden = () =>
			HttpResponse.json(
				{ type: 'forbidden', title: 'Forbidden', status: 403, detail: 'Not yours.' },
				{ status: 403, headers: { 'Content-Type': 'application/problem+json' } },
			);
		worker.use(
			http.post('/connect-sessions/:id\\:cancel', forbidden),
			http.post('/connect-sessions/:id\\:reject', forbidden),
		);

		for (const run of [
			() => cancelConnectSession('cs_1'),
			() => rejectConnectSession('cs_1'),
		]) {
			const err = await run().catch((e: unknown) => e);
			expect(err).toBeInstanceOf(IntegrationsApiError);
			expect((err as IntegrationsApiError).status).toBe(403);
			expect((err as IntegrationsApiError).code).toBe('forbidden');
			expect((err as IntegrationsApiError).message).toBe('Not yours.');
		}
	});

	it('sends the OAuth app registration id only when one is pinned', async () => {
		const seen: (string | null)[] = [];
		worker.use(
			http.get('/vendors/:key/auth-capabilities', ({ request, params }) => {
				expect(params.key).toBe('github.com');
				seen.push(new URL(request.url).searchParams.get('oauth_app_registration_id'));
				return HttpResponse.json({
					vendor: 'github.com',
					display_name: 'GitHub',
					flows: [],
					scopes: [],
				});
			}),
		);

		await getVendorAuthCapabilities('github.com', 'oar_1');
		await getVendorAuthCapabilities('github.com', null);
		await getVendorAuthCapabilities('github.com');

		expect(seen).toEqual(['oar_1', null, null]);
	});

	it('maps a connect-request list failure to IntegrationsApiError', async () => {
		worker.use(
			http.get('/connect-sessions', () =>
				HttpResponse.json(
					{ type: 'forbidden', title: 'Forbidden', status: 403, detail: 'No access.' },
					{ status: 403, headers: { 'Content-Type': 'application/problem+json' } },
				),
			),
		);

		const err = await listOpenConnectRequests().catch((e: unknown) => e);

		expect(err).toBeInstanceOf(IntegrationsApiError);
		expect((err as IntegrationsApiError).status).toBe(403);
	});

	it('follows operation pages and reads a 404 as not yet imported', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/operations', ({ request, params }) => {
				if (params.name === 'missing') {
					return HttpResponse.json(
						{ type: 'no_current_revision', status: 404 },
						{ status: 404 },
					);
				}
				const cursor = new URL(request.url).searchParams.get('cursor');
				const op = (id: string) => ({ operation_id: id, method: 'GET', path: `/${id}` });
				return HttpResponse.json(
					cursor
						? { data: [op('b')], has_more: false, next_cursor: null }
						: { data: [op('a')], has_more: true, next_cursor: 'c2' },
				);
			}),
		);

		const all = await listAllVendorOperations('github.com', 'github', '1');
		expect(all?.data.map((o) => o.operation_id)).toEqual(['a', 'b']);
		expect(all?.has_more).toBe(false);
		await expect(listAllVendorOperations('github.com', 'missing', '1')).resolves.toBeNull();
	});
});

describe('listAgentsForPicker', () => {
	it('follows next_cursor past the first page, so large orgs are not truncated', async () => {
		const agent = (n: number) => ({
			id: `agnt_${n}`,
			name: `agent-${n}`,
			status: 'active',
		});
		const limits: string[] = [];
		worker.use(
			http.get('/agents', ({ request }) => {
				const params = new URL(request.url).searchParams;
				limits.push(params.get('limit') ?? '');
				const start = Number(params.get('cursor') ?? 0);
				const end = Math.min(start + 200, 250);
				return HttpResponse.json({
					data: Array.from({ length: end - start }, (_, i) => agent(start + i)),
					has_more: end < 250,
					next_cursor: end < 250 ? String(end) : null,
				});
			}),
		);

		const res = await listAgentsForPicker();

		expect(res.data).toHaveLength(250);
		expect(res.data[res.data.length - 1]?.id).toBe('agnt_249');
		expect(res.has_more).toBe(false);
		expect(limits).toEqual(['200', '200']);
	});
});
