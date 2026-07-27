import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { clearToken, setToken } from '@/shared/api';
import { ProvisioningRequestDialog } from '@/shared/app/rail/ProvisioningRequestDialog';
import type { AccessRequest } from '@/shared/lib/accessRequests';

/**
 * The wizard's toolkit-name lifecycle has two async races worth pinning:
 *
 * 1. The actor directory resolves AFTER the seed effect, so the suggested name
 *    upgrades from the API slug to "<Agent> toolkit" — unless the operator
 *    already edited the field.
 * 2. `createPlanToolkit` may return a 409-disambiguated name ("… toolkit-2");
 *    the wizard must adopt the ACTUAL created name, because the review step
 *    and the no-auth credential name derive from this state. With agent-first
 *    naming, a second request from the same agent makes this the common path.
 */

const AGENT_ID = 'agnt_wizard_test';

/** A no-auth provisioning plan (no manual credential step). */
function planRequest(): AccessRequest {
	const ref = { vendor: 'open-meteo-com', name: 'forecast' };
	return {
		id: 'arq_plan_naming',
		actor_id: AGENT_ID,
		status: 'pending',
		requested_by: AGENT_ID,
		reason: 'need weather data',
		filed_at: new Date().toISOString(),
		expires_at: new Date(Date.now() + 3_600_000).toISOString(),
		items: [
			{
				id: 'i1',
				resource_type: 'toolkit',
				action: 'create',
				status: 'pending',
				resource_reference: ref,
			},
			{
				id: 'i2',
				resource_type: 'credential',
				action: 'provision',
				status: 'pending',
				resource_reference: { ...ref, security_scheme: 'no_auth' },
			},
			{
				id: 'i3',
				resource_type: 'credential',
				action: 'bind',
				status: 'pending',
				resource_reference: ref,
			},
			{
				id: 'i4',
				resource_type: 'toolkit',
				action: 'bind',
				status: 'pending',
				resource_reference: ref,
			},
		],
	};
}

function stubDirectoryAndRequest(request: AccessRequest) {
	worker.use(
		http.get('/actors', () =>
			// Paginated envelope — fetchActorDirectory walks `data`/`next_cursor`.
			HttpResponse.json({
				data: [
					{
						id: AGENT_ID,
						actor_type: 'agent',
						name: 'Weather Agent',
						active: true,
						created_at: '2026-01-01T00:00:00Z',
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		),
		http.get('/access-requests/:id', () => HttpResponse.json(request)),
	);
}

describe('ProvisioningRequestDialog — toolkit-name lifecycle', () => {
	// The actor directory query is gated on holding a bearer token.
	beforeEach(() => setToken('test-token'));
	afterEach(() => clearToken());

	it('upgrades the suggested name once the actor directory resolves', async () => {
		stubDirectoryAndRequest(planRequest());
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);

		const input = await screen.findByLabelText('Toolkit name');
		await waitFor(() => expect(input).toHaveValue('Weather Agent toolkit'));
	});

	it('never clobbers a manual edit with the late directory resolution', async () => {
		stubDirectoryAndRequest(planRequest());
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		const input = await screen.findByLabelText('Toolkit name');
		await user.clear(input);
		await user.type(input, 'my custom kit');
		// Give the directory query time to land; the edit must survive it.
		await new Promise((r) => setTimeout(r, 50));
		expect(input).toHaveValue('my custom kit');
	});

	it('adopts the 409-disambiguated name the toolkit was actually created with', async () => {
		stubDirectoryAndRequest(planRequest());
		let attempts = 0;
		worker.use(
			http.post('/toolkits', () => {
				attempts += 1;
				if (attempts === 1) {
					return HttpResponse.json({ detail: 'conflict' }, { status: 409 });
				}
				return HttpResponse.json({
					toolkit: {
						toolkit_id: 'tk_new',
						name: 'Weather Agent toolkit-2',
					},
					api_key: 'k',
				});
			}),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		const input = await screen.findByLabelText('Toolkit name');
		await waitFor(() => expect(input).toHaveValue('Weather Agent toolkit'));
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));

		// No-auth plan: toolkit → rules. Continue to the review summary.
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		// The review must show the name the server actually assigned, not the
		// pre-collision suggestion.
		expect(await screen.findByText('Weather Agent toolkit-2')).toBeInTheDocument();
		expect(attempts).toBe(2);
	});
});
