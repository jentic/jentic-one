import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { PostConnectBindMore } from '@/shared/credentials/components';

/**
 * "Bind to more agents" after a connect: the new bindings get the end date
 * chosen here, and binding with none asks first.
 */
describe('PostConnectBindMore — how long the access lasts', () => {
	const DAY = 86_400_000;
	let posts: { agent: string; body: Record<string, unknown> }[] = [];

	beforeEach(() => {
		setToken('test-token');
		posts = [];
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: ['agent_primary', 'agent_extra'].map((id) => ({
						id,
						name: id,
						status: 'active',
						created_at: '2026-01-01T00:00:00Z',
					})),
					has_more: false,
					next_cursor: null,
				}),
			),
			http.post('/agents/:id/credentials', async ({ params, request }) => {
				posts.push({
					agent: String(params.id),
					body: (await request.json()) as Record<string, unknown>,
				});
				return HttpResponse.json({}, { status: 201 });
			}),
		);
	});

	function renderCta() {
		return renderWithProviders(
			<PostConnectBindMore credentialId="cred_new" boundAgentId="agent_primary" />,
		);
	}

	it('binds the ticked agents until the chosen end date', async () => {
		const user = userEvent.setup();
		renderCta();
		await user.click(await screen.findByText('agent_extra'));
		await user.selectOptions(screen.getByTestId('bind-more-duration-preset'), '1d');
		const before = Date.now();
		await user.click(screen.getByRole('button', { name: 'Bind to 1 agent' }));

		await waitFor(() => expect(posts).toHaveLength(1));
		expect(posts[0].agent).toBe('agent_extra');
		const at = Date.parse(String(posts[0].body.expires_at));
		expect(at).toBeGreaterThanOrEqual(before + DAY);
		expect(at).toBeLessThanOrEqual(Date.now() + DAY);
		expect(screen.queryByTestId('no-expiry-warning')).not.toBeInTheDocument();
		expect(await screen.findByText('Bound to selected agents.')).toBeVisible();
	});

	it('waits for a duration; No expiry asks first and binds only once confirmed', async () => {
		const user = userEvent.setup();
		renderCta();
		await user.click(await screen.findByText('agent_extra'));
		expect(screen.getByRole('button', { name: 'Bind to 1 agent' })).toBeDisabled();

		await user.selectOptions(screen.getByTestId('bind-more-duration-preset'), 'custom');
		await user.selectOptions(screen.getByTestId('bind-more-duration-unit'), 'never');
		await user.click(screen.getByRole('button', { name: 'Bind to 1 agent' }));
		expect(await screen.findByTestId('no-expiry-warning')).toHaveTextContent(
			'This agent will keep this access until someone removes it.',
		);
		expect(posts).toHaveLength(0);
		await user.click(screen.getByRole('button', { name: 'Bind without expiry' }));
		await waitFor(() => expect(posts).toHaveLength(1));
		// A confirmed "No expiry" is said, not left out (an org default would fill a gap).
		expect(posts[0].body).toEqual({ credential_id: 'cred_new', expires_at: null });
	});
});
