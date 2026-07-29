import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { clearToken, setToken } from '@/shared/api';
import {
	ProvisioningRequestDialog,
	resetProvisioningWizardDrafts,
} from '@/shared/app/rail/ProvisioningRequestDialog';
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
		created_by: AGENT_ID,
		approve_url: 'https://app.example.test/access-requests/arq_plan_naming',
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

function stubDirectoryAndRequest(request: AccessRequest, opts?: { directoryMisses?: boolean }) {
	worker.use(
		http.get('/actors', () =>
			// Paginated envelope — fetchActorDirectory walks `data`/`next_cursor`.
			HttpResponse.json({
				data: opts?.directoryMisses
					? []
					: [
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
		// The wizard's directory-miss fallback fetches the agent directly.
		http.get('/agents/:id', () =>
			HttpResponse.json({
				id: AGENT_ID,
				name: 'Weather Agent',
				status: 'approved',
				registered_by: 'usr_admin',
				created_at: '2026-01-01T00:00:00Z',
			}),
		),
		http.get('/access-requests/:id', () => HttpResponse.json(request)),
	);
}

describe('ProvisioningRequestDialog — toolkit-name lifecycle', () => {
	// The actor directory query is gated on holding a bearer token.
	beforeEach(() => {
		setToken('test-token');
		// Drafts are module-scoped by design; tests share request fixtures.
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	it('upgrades the suggested name once the actor directory resolves', async () => {
		stubDirectoryAndRequest(planRequest());
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);

		const input = await screen.findByLabelText('Toolkit name');
		await waitFor(() => expect(input).toHaveValue('Weather Agent toolkit'));
	});

	it('falls back to a direct agent fetch when the cached directory misses', async () => {
		// The real-world race: the agent registered seconds ago, so the cached
		// directory predates it. The wizard must not settle for the raw
		// `agnt_…` id / API-slug name — it fetches the agent by id itself.
		stubDirectoryAndRequest(planRequest(), { directoryMisses: true });
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

describe('ProvisioningRequestDialog — cancel with orphans', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	it('asks in-dialog (never window.confirm) and discards the created toolkit', async () => {
		stubDirectoryAndRequest(planRequest());
		let deleted: string | null = null;
		worker.use(
			http.post('/toolkits', () =>
				HttpResponse.json({
					toolkit: { toolkit_id: 'tk_orphan', name: 'Weather Agent toolkit' },
					api_key: 'k',
				}),
			),
			http.delete('/toolkits/:id', ({ params }) => {
				deleted = String(params.id);
				return new HttpResponse(null, { status: 204 });
			}),
		);
		let closed = false;
		renderWithProviders(
			<ProvisioningRequestDialog
				open
				request={planRequest()}
				onClose={() => {
					closed = true;
				}}
			/>,
		);
		const user = userEvent.setup();

		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await screen.findByRole('button', { name: /^Review/ });

		// Cancel the wizard mid-fulfilment → the in-dialog confirmation appears.
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(await screen.findByText('Keep this setup for later?')).toBeInTheDocument();
		expect(closed).toBe(false);

		await user.click(screen.getByRole('button', { name: /Discard/ }));
		await waitFor(() => expect(closed).toBe(true));
		expect(deleted).toBe('tk_orphan');
	});

	it('"Keep & finish later" closes without deleting anything', async () => {
		stubDirectoryAndRequest(planRequest());
		let deleteCalls = 0;
		worker.use(
			http.post('/toolkits', () =>
				HttpResponse.json({
					toolkit: { toolkit_id: 'tk_keep', name: 'Weather Agent toolkit' },
					api_key: 'k',
				}),
			),
			http.delete('/toolkits/:id', () => {
				deleteCalls += 1;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		let closed = false;
		renderWithProviders(
			<ProvisioningRequestDialog
				open
				request={planRequest()}
				onClose={() => {
					closed = true;
				}}
			/>,
		);
		const user = userEvent.setup();

		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await screen.findByRole('button', { name: /^Review/ });

		await user.click(screen.getByRole('button', { name: 'Close' }));
		await user.click(await screen.findByRole('button', { name: /Keep & finish later/ }));
		await waitFor(() => expect(closed).toBe(true));
		expect(deleteCalls).toBe(0);
	});

	it('resumes the kept draft on reopen instead of creating a second toolkit', async () => {
		stubDirectoryAndRequest(planRequest());
		let createCalls = 0;
		worker.use(
			http.post('/toolkits', () => {
				createCalls += 1;
				return HttpResponse.json({
					toolkit: { toolkit_id: 'tk_resume', name: 'Weather Agent toolkit' },
					api_key: 'k',
				});
			}),
		);
		const user = userEvent.setup();

		// First session: create the toolkit, then "Keep & finish later". The
		// production mount path (AccessRequestDecisionDialog) UNMOUNTS the
		// wizard on close, so we simulate that with a full unmount.
		const first = renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await screen.findByRole('button', { name: /^Review/ });
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await user.click(await screen.findByRole('button', { name: /Keep & finish later/ }));
		first.unmount();

		// Second session for the SAME request: the draft must restore the rules
		// step with the existing toolkit — never re-run the create step, which
		// would strand tk_resume and accumulate a second toolkit.
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);
		expect(await screen.findByRole('button', { name: /^Review/ })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /Create toolkit/i })).not.toBeInTheDocument();
		expect(createCalls).toBe(1);
	});
});

/** A composite: two no-auth chains + a scope grant, as the CLI now files it. */
function compositeRequest(): AccessRequest {
	const refA = { vendor: 'open-meteo-com', name: 'forecast' };
	const refB = { vendor: 'country-is', name: 'country-is' };
	const chain = (ref: { vendor: string; name: string }, p: string) => [
		{
			id: `${p}1`,
			resource_type: 'toolkit',
			action: 'create',
			status: 'pending',
			resource_reference: ref,
		},
		{
			id: `${p}2`,
			resource_type: 'credential',
			action: 'provision',
			status: 'pending',
			resource_reference: { ...ref, security_scheme: 'no_auth' },
		},
		{
			id: `${p}3`,
			resource_type: 'credential',
			action: 'bind',
			status: 'pending',
			resource_reference: ref,
		},
		{
			id: `${p}4`,
			resource_type: 'toolkit',
			action: 'bind',
			status: 'pending',
			resource_reference: ref,
		},
	];
	return {
		id: 'arq_composite',
		actor_id: AGENT_ID,
		status: 'pending',
		requested_by: AGENT_ID,
		created_by: AGENT_ID,
		approve_url: 'https://app.example.test/access-requests/arq_composite',
		reason: 'two APIs, one job',
		filed_at: new Date().toISOString(),
		expires_at: new Date(Date.now() + 3_600_000).toISOString(),
		items: [
			...chain(refA, 'a'),
			...chain(refB, 'b'),
			{
				id: 's1',
				resource_type: 'scope',
				action: 'grant',
				status: 'pending',
				resource_id: 'catalog:import',
			},
		],
	} as AccessRequest;
}

describe('ProvisioningRequestDialog — multi-chain composite', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	function stubComposite(opts?: {
		onAmend?: (body: unknown) => void;
		onDecide?: (body: unknown) => void;
	}) {
		const request = compositeRequest();
		let created = 0;
		stubDirectoryAndRequest(request);
		worker.use(
			http.post('/toolkits', () => {
				created += 1;
				return HttpResponse.json({
					toolkit: {
						toolkit_id: `tk_chain_${created}`,
						name: `Chain toolkit ${created}`,
					},
					api_key: 'k',
				});
			}),
			// Both chains are no-auth: the submit path auto-creates a NO_AUTH
			// credential per fulfilled chain.
			http.post('/credentials', () =>
				HttpResponse.json({
					credential: { credential_id: `cred_noauth_${created}` },
				}),
			),
			http.post('/access-requests/*', async ({ request: httpReq }) => {
				const url = new URL(httpReq.url);
				const body = await httpReq.json();
				if (url.pathname.endsWith(':amend')) {
					opts?.onAmend?.(body);
					return HttpResponse.json(request);
				}
				if (url.pathname.endsWith(':decide')) {
					opts?.onDecide?.(body);
					const decisions = (body as { items: { item_id: string; decision: string }[] })
						.items;
					const byId = new Map(decisions.map((d) => [d.item_id, d.decision]));
					return HttpResponse.json({
						...request,
						status: decisions.every((d) => d.decision === 'approved')
							? 'approved'
							: 'partially_approved',
						items: request.items.map((it) => ({
							...it,
							status: byId.get(it.id) ?? it.status,
						})),
					});
				}
				return new HttpResponse(null, { status: 404 });
			}),
		);
		return request;
	}

	it('walks both chains, then approves everything in one amend + one decide', async () => {
		let amendBody: unknown;
		let decideBody: unknown;
		const request = stubComposite({
			onAmend: (b) => (amendBody = b),
			onDecide: (b) => (decideBody = b),
		});
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Chain 1 (no-auth): toolkit → rules → "Next API".
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /Next API/ }));

		// Chain 2: toolkit → rules → Review.
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Review lists both chains and the extra scope grant. (The APIs also
		// appear as subtitle badges, so assert on multiplicity, not uniqueness.)
		expect((await screen.findAllByText('open-meteo-com/forecast')).length).toBeGreaterThan(1);
		expect(screen.getAllByText('country-is/country-is').length).toBeGreaterThan(1);
		expect(screen.getByText(/scope catalog:import/)).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// One amend carrying BOTH chains' bind items, each keyed to its own
		// toolkit — never cross-wired.
		const amendments = (
			amendBody as { items: { item_id: string; to_id?: string; resource_id?: string }[] }
		).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('a3')?.to_id).toBe('tk_chain_1');
		expect(byItem.get('a4')?.resource_id).toBe('tk_chain_1');
		expect(byItem.get('b3')?.to_id).toBe('tk_chain_2');
		expect(byItem.get('b4')?.resource_id).toBe('tk_chain_2');

		// One decide approving every pending item, the scope grant included.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		expect(decisions).toHaveLength(9);
		expect(decisions.every((d) => d.decision === 'approved')).toBe(true);
	});

	it('skipping a chain denies its items and grants the rest', async () => {
		let amendBody: unknown;
		let decideBody: unknown;
		const request = stubComposite({
			onAmend: (b) => (amendBody = b),
			onDecide: (b) => (decideBody = b),
		});
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Fulfil chain 1, then SKIP chain 2 straight from its first step.
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Skip this API/ }));

		// Review flags the skipped chain and still allows submitting.
		expect(await screen.findByText(/skipped — will be denied/)).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// Only chain 1 was amended…
		const amendments = (amendBody as { items: { item_id: string }[] }).items;
		expect(amendments.map((a) => a.item_id).sort()).toEqual(['a3', 'a4']);
		// …and the decide denies exactly chain 2's four items.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		const denied = decisions.filter((d) => d.decision === 'denied').map((d) => d.item_id);
		expect(denied.sort()).toEqual(['b1', 'b2', 'b3', 'b4']);
		expect(decisions.filter((d) => d.decision === 'approved')).toHaveLength(5);
	});
});
