import { beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { bindCredentialToAgentBlocked } from '@/shared/credentials/api/vendors-client';

/**
 * Binding a credential to an agent says why the server refused: an org limit
 * ("at most 30 days") reads better than a status line, and a bare refusal of
 * the end date still gets the lapsed-clock explanation.
 */
describe('bindCredentialToAgentBlocked — a refused bind', () => {
	beforeEach(() => setToken('test-token'));

	it("shows the server's own words for an org limit", async () => {
		worker.use(
			http.post('/agents/:id/credentials', () =>
				HttpResponse.json(
					{
						type: 'grant_policy_limit_exceeded',
						title: "Outside the org's access limits",
						status: 422,
						detail: 'This org allows a grant to last at most 30 days: choose an earlier end date',
					},
					{ status: 422, headers: { 'Content-Type': 'application/problem+json' } },
				),
			),
		);

		await expect(
			bindCredentialToAgentBlocked('agent_1', 'cred_1', {
				expiresAt: '2099-01-01T00:00:00Z',
			}),
		).rejects.toThrow('at most 30 days');
	});

	it('explains a lapsed end date when the server only names the field', async () => {
		worker.use(
			http.post('/agents/:id/credentials', () =>
				HttpResponse.json(
					{
						detail: [
							{
								loc: ['body', 'expires_at'],
								msg: 'expires_at must be in the future',
							},
						],
					},
					{ status: 422 },
				),
			),
		);

		await expect(
			bindCredentialToAgentBlocked('agent_1', 'cred_1', {
				expiresAt: '2000-01-01T00:00:00Z',
			}),
		).rejects.toThrow('already passed');
	});

	it('falls back to a plain message for any other failure', async () => {
		worker.use(
			http.post('/agents/:id/credentials', () => HttpResponse.json({}, { status: 500 })),
		);

		await expect(bindCredentialToAgentBlocked('agent_1', 'cred_1')).rejects.toThrow();
	});
});
