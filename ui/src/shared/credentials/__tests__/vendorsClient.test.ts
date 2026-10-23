import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	IntegrationsApiError,
	confirmConnectSession,
	getConnectSession,
	listAllVendorOperations,
	pollConnectSessionStatus,
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
