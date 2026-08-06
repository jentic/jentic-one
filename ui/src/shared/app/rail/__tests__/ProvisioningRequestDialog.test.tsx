import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
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
		// toolkit — never cross-wired. The inert placeholders are stamped with
		// the ids that fulfilled them (audit honesty, #897).
		const amendments = (
			amendBody as { items: { item_id: string; to_id?: string; resource_id?: string }[] }
		).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('a3')?.to_id).toBe('tk_chain_1');
		expect(byItem.get('a4')?.resource_id).toBe('tk_chain_1');
		expect(byItem.get('b3')?.to_id).toBe('tk_chain_2');
		expect(byItem.get('b4')?.resource_id).toBe('tk_chain_2');
		expect(byItem.get('a1')?.resource_id).toBe('tk_chain_1');
		expect(byItem.get('b1')?.resource_id).toBe('tk_chain_2');
		expect(byItem.get('a2')?.resource_id).toMatch(/^cred_noauth_/);
		expect(byItem.get('b2')?.resource_id).toMatch(/^cred_noauth_/);

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

		// Only chain 1 was amended (its placeholders included — never the
		// skipped chain's)…
		const amendments = (amendBody as { items: { item_id: string }[] }).items;
		expect(amendments.map((a) => a.item_id).sort()).toEqual(['a1', 'a2', 'a3', 'a4']);
		// …and the decide denies exactly chain 2's four items.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		const denied = decisions.filter((d) => d.decision === 'denied').map((d) => d.item_id);
		expect(denied.sort()).toEqual(['b1', 'b2', 'b3', 'b4']);
		expect(decisions.filter((d) => d.decision === 'approved')).toHaveLength(5);
	});

	it('backing into a skipped chain lets the operator include it again', async () => {
		let decideBody: unknown;
		const request = stubComposite({ onDecide: (b) => (decideBody = b) });
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Fulfil chain 1, skip chain 2, then change your mind from review.
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Skip this API/ }));
		await screen.findByText(/skipped — will be denied/);
		await user.click(screen.getByRole('button', { name: /Back/ }));

		// The skipped chain's step offers the un-skip affordance; taking it
		// restores the normal create flow, and fulfilment proceeds as usual.
		await user.click(await screen.findByRole('button', { name: /Include this API/ }));
		await user.click(await screen.findByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		expect(screen.queryByText(/skipped — will be denied/)).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// Nothing is denied — the un-skipped chain was fulfilled and approved.
		const decisions = (decideBody as { items: { decision: string }[] }).items;
		expect(decisions).toHaveLength(9);
		expect(decisions.every((d) => d.decision === 'approved')).toBe(true);
	});

	it('persists the draft to sessionStorage so a same-tab redirect can resume', async () => {
		const request = stubComposite();
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await screen.findByRole('button', { name: /Next API/ });

		// The draft (with the created toolkit id) must be in sessionStorage —
		// a module-scoped map would not survive the OAuth popup-blocked
		// same-tab redirect fallback. (The persist effect is passive; wait
		// for it to flush.)
		await waitFor(() => {
			expect(sessionStorage.getItem('jentic.provisioningWizardDrafts')).not.toBeNull();
		});
		const raw = sessionStorage.getItem('jentic.provisioningWizardDrafts');
		const stored = JSON.parse(raw!) as Record<
			string,
			{ chains: { key: string; toolkitId: string | null }[] }
		>;
		const draft = stored[request.id];
		expect(draft).toBeDefined();
		expect(draft.chains[0].toolkitId).toBe('tk_chain_1');
		expect(draft.chains[0].key).toContain('open-meteo-com');
	});
});

/** A single-chain plan whose credential must be operator-provided (api_key). */
function authPlanRequest(): AccessRequest {
	const base = planRequest();
	return {
		...base,
		id: 'arq_plan_auth',
		approve_url: 'https://app.example.test/access-requests/arq_plan_auth',
		items: base.items.map((it) =>
			it.action === 'provision'
				? {
						...it,
						resource_reference: {
							...it.resource_reference,
							security_scheme: 'api_key',
						},
					}
				: it,
		),
	};
}

describe('ProvisioningRequestDialog — adopt existing objects (#826)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	/** Stub the pickers' list endpoints + the amend/decide submit path. */
	function stubAdoption(
		request: AccessRequest,
		opts?: {
			onAmend?: (body: unknown) => void;
			onDecide?: (body: unknown) => void;
			onCredentialQuery?: (vendor: string | null) => void;
		},
	) {
		stubDirectoryAndRequest(request);
		worker.use(
			http.get('/toolkits', () =>
				HttpResponse.json({
					data: [
						{
							toolkit_id: 'tk_existing',
							name: 'Ops toolkit',
							description: null,
							active: true,
							created_by: 'usr_admin',
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							credential_count: 1,
							key_count: 0,
						},
						{
							// Suspended — must never be offered for adoption.
							toolkit_id: 'tk_suspended',
							name: 'Suspended toolkit',
							description: null,
							active: false,
							created_by: 'usr_admin',
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							credential_count: 0,
							key_count: 0,
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials', ({ request: httpReq }) => {
				opts?.onCredentialQuery?.(new URL(httpReq.url).searchParams.get('vendor'));
				return HttpResponse.json({
					data: [
						{
							credential_id: 'cred_exist',
							name: 'Weather key',
							type: 'api_key',
							provider: 'manual',
							active: true,
							api: { vendor: 'open-meteo-com', name: 'forecast', version: null },
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
						},
						{
							// Disabled for injection — must never be offered.
							credential_id: 'cred_disabled',
							name: 'Old disabled key',
							type: 'api_key',
							provider: 'manual',
							active: false,
							api: { vendor: 'open-meteo-com', name: 'forecast', version: null },
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
						},
					],
					has_more: false,
					next_cursor: null,
				});
			}),
			http.post('/credentials', () =>
				HttpResponse.json({ credential: { credential_id: 'cred_noauth_1' } }),
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
						status: 'approved',
						items: request.items.map((it) => ({
							...it,
							status: byId.get(it.id) ?? it.status,
						})),
					});
				}
				return new HttpResponse(null, { status: 404 });
			}),
		);
	}

	it('adopting an existing toolkit skips the create call and amends its id', async () => {
		let amendBody: unknown;
		let createCalls = 0;
		const request = planRequest();
		stubAdoption(request, { onAmend: (b) => (amendBody = b) });
		worker.use(
			http.post('/toolkits', () => {
				createCalls += 1;
				return HttpResponse.json(
					{ toolkit: { toolkit_id: 'tk_new', name: 'nope' }, api_key: 'k' },
					{ status: 201 },
				);
			}),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Pick the existing toolkit instead of creating one — selection is
		// staged, the button commits (no-auth plan: straight to rules).
		const picker = await screen.findByLabelText(/use an existing toolkit/i);
		// No satisfaction hint on this request — the nudge must not render.
		expect(screen.queryByText(/already wired/i)).not.toBeInTheDocument();
		await user.selectOptions(picker, 'tk_existing');
		await user.click(screen.getByRole('button', { name: 'Use this toolkit' }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Review names the adopted toolkit and marks it as pre-existing.
		expect(await screen.findByText('Ops toolkit')).toBeInTheDocument();
		expect(screen.getByText('(existing)')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// The binds were amended to the ADOPTED id; nothing was created. The
		// toolkit:create placeholder is stamped with the REUSED toolkit id so
		// the approved record reads "fulfilled by tk_existing", not a phantom
		// create (#897 audit honesty).
		const amendments = (
			amendBody as { items: { item_id: string; to_id?: string; resource_id?: string }[] }
		).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('i3')?.to_id).toBe('tk_existing');
		expect(byItem.get('i4')?.resource_id).toBe('tk_existing');
		expect(byItem.get('i1')?.resource_id).toBe('tk_existing');
		expect(createCalls).toBe(0);
	});

	it('never offers to discard adopted objects on cancel', async () => {
		const request = planRequest();
		stubAdoption(request);
		let closed = false;
		let deleteCalls = 0;
		worker.use(
			http.delete('/toolkits/:id', () => {
				deleteCalls += 1;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		renderWithProviders(
			<ProvisioningRequestDialog
				open
				request={request}
				onClose={() => {
					closed = true;
				}}
			/>,
		);
		const user = userEvent.setup();

		const picker = await screen.findByLabelText(/use an existing toolkit/i);
		await user.selectOptions(picker, 'tk_existing');
		await user.click(screen.getByRole('button', { name: 'Use this toolkit' }));
		await screen.findByRole('button', { name: /^Review/ });

		// Cancel: the wizard created NOTHING this session (the toolkit was
		// adopted), so there are no orphans — close directly, never offering
		// to delete infrastructure the operator set up outside the wizard.
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(closed).toBe(true));
		// The confirm <dialog> stays mounted while closed, so assert on
		// visibility rather than presence.
		expect(screen.getByText('Keep this setup for later?')).not.toBeVisible();
		expect(deleteCalls).toBe(0);
	});

	it('adopting an existing credential skips the connect flow and amends its id', async () => {
		let amendBody: unknown;
		const request = authPlanRequest();
		stubAdoption(request, { onAmend: (b) => (amendBody = b) });
		worker.use(
			http.post('/toolkits', () =>
				HttpResponse.json({
					toolkit: { toolkit_id: 'tk_auth', name: 'Weather Agent toolkit' },
					api_key: 'k',
				}),
			),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));

		// The credential step offers the vendor-scoped existing credentials;
		// staging one and committing advances straight to rules — no create
		// form, no connect flow.
		const picker = await screen.findByLabelText(/use an existing credential/i);
		await user.selectOptions(picker, 'cred_exist');
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		expect(await screen.findByText('Weather key')).toBeInTheDocument();
		expect(screen.getByText('(existing)')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		const amendments = (amendBody as { items: { item_id: string; resource_id?: string }[] })
			.items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('i3')?.resource_id).toBe('cred_exist');
		// The credential:provision placeholder records the reused credential.
		expect(byItem.get('i2')?.resource_id).toBe('cred_exist');
	});

	it('slugifies the raw filed vendor and hides inactive artifacts in the pickers', async () => {
		// Agents file references with raw domains ('Open-Meteo.com'); stored
		// rows carry the slug ('open-meteo-com') and the credential list's
		// vendor filter is an exact match — an unslugged query would silently
		// collapse the picker (issue #656's mismatch).
		let queriedVendor: string | null | undefined;
		const base = authPlanRequest();
		const rawRef = { vendor: 'Open-Meteo.com', name: 'forecast' };
		const request = {
			...base,
			items: base.items.map((it) => ({
				...it,
				resource_reference: { ...it.resource_reference, ...rawRef },
			})),
		};
		stubAdoption(request, { onCredentialQuery: (v) => (queriedVendor = v) });
		worker.use(
			http.post('/toolkits', () =>
				HttpResponse.json({
					toolkit: { toolkit_id: 'tk_auth', name: 'Weather Agent toolkit' },
					api_key: 'k',
				}),
			),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Toolkit picker: the suspended toolkit is never offered.
		const toolkitPicker = await screen.findByLabelText(/use an existing toolkit/i);
		expect(within(toolkitPicker).getByRole('option', { name: 'Ops toolkit' })).toBeVisible();
		expect(
			within(toolkitPicker).queryByRole('option', { name: 'Suspended toolkit' }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));

		// Credential picker: queried with the slug, disabled rows filtered out.
		const credPicker = await screen.findByLabelText(/use an existing credential/i);
		expect(queriedVendor).toBe('open-meteo-com');
		expect(within(credPicker).getByRole('option', { name: /Weather key/ })).toBeVisible();
		expect(
			within(credPicker).queryByRole('option', { name: /Old disabled key/ }),
		).not.toBeInTheDocument();
	});

	it('names the wired toolkit, floats it in the picker, and reviews honestly on adopt', async () => {
		// The backend hint carries WHICH toolkit satisfies the bind
		// (already_satisfied_by) — the nudge names it and the picker floats it
		// so the operator isn't left hunting through name-only options.
		const base = planRequest();
		const request = {
			...base,
			items: base.items.map((it) =>
				it.id === 'i4'
					? { ...it, already_satisfied: true, already_satisfied_by: 'tk_existing' }
					: it,
			),
		};
		stubAdoption(request);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		const nudge = await screen.findByText(/already wired to/i);
		expect(nudge).toHaveTextContent('Ops toolkit');

		const picker = await screen.findByLabelText(/use an existing toolkit/i);
		const options = within(picker).getAllByRole('option');
		expect(options[1]).toHaveTextContent(/Ops toolkit — already linked to this agent/);

		// Adopt it and reach review: the note is the adopted variant, honest
		// about the rules being updated (an approve REPLACES binding rules —
		// never "nothing changes").
		await user.selectOptions(picker, 'tk_existing');
		await user.click(screen.getByRole('button', { name: 'Use this toolkit' }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		expect(
			await screen.findByText(/reuses that setup and updates its permission rules/i),
		).toBeInTheDocument();
	});

	it('ranks toolkits already serving the chain API first and badges them', async () => {
		// The canonical #826 manual state (toolkit + credential exist, agent
		// unbound) satisfies nothing, so the wired-toolkit float never fires —
		// ranking by the served API (the list's `apis` aggregation) rescues
		// that exact case: the right toolkit surfaces even from a name-only
		// list (#890).
		const request = planRequest();
		stubDirectoryAndRequest(request);
		worker.use(
			http.get('/toolkits', () =>
				HttpResponse.json({
					data: [
						{
							// Server order puts the non-serving toolkit first: the
							// ranking, not luck, must float the serving one.
							toolkit_id: 'tk_unrelated',
							name: 'Unrelated toolkit',
							description: null,
							active: true,
							created_by: 'usr_admin',
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							credential_count: 1,
							key_count: 0,
							apis: [{ api_vendor: 'github-com', api_name: 'rest' }],
						},
						{
							toolkit_id: 'tk_serves',
							name: 'Weather toolkit',
							description: null,
							active: true,
							created_by: 'usr_admin',
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							credential_count: 1,
							key_count: 0,
							apis: [{ api_vendor: 'open-meteo-com', api_name: 'forecast' }],
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);

		const picker = await screen.findByLabelText(/use an existing toolkit/i);
		const options = within(picker).getAllByRole('option');
		// [0] is the placeholder; the serving toolkit floats above the rest.
		// The badge is hedged/fixed-width ("this API", not the chain label):
		// NULL-name credentials match laxly, and long names would push a long
		// suffix past the closed control's ellipsis.
		expect(options[1]).toHaveTextContent(/Weather toolkit — already serves this API/);
		expect(options[2]).toHaveTextContent('Unrelated toolkit');
		expect(options[2]).not.toHaveTextContent(/already serves/);
	});

	it('flags a never-connected OAuth credential and warns before adoption', async () => {
		// The adopt picker previously trusted the operator's choice: a
		// never-signed-in OAuth credential only failed at execute time. The
		// redacted listing now carries the derived connect state, so the
		// picker warns BEFORE the pick is committed (#890).
		const request = authPlanRequest();
		stubAdoption(request);
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						{
							credential_id: 'cred_oauth_pending',
							name: 'GitHub OAuth',
							type: 'oauth2',
							provider: 'static',
							active: true,
							api: { vendor: 'open-meteo-com', name: 'forecast', version: null },
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							details: {
								client_id: 'cid',
								token_url: 'https://auth.example/token',
								grant_type: 'authorization_code',
								connected: false,
							},
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.post('/toolkits', () =>
				HttpResponse.json({
					toolkit: { toolkit_id: 'tk_auth', name: 'Weather Agent toolkit' },
					api_key: 'k',
				}),
			),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));

		const picker = await screen.findByLabelText(/use an existing credential/i);
		const options = within(picker).getAllByRole('option');
		expect(options[1]).toHaveTextContent(/not connected yet/);

		// No warning until the risky option is actually staged.
		expect(screen.queryByText(/was never connected/i)).not.toBeInTheDocument();
		await user.selectOptions(picker, 'cred_oauth_pending');
		expect(await screen.findByText(/was never connected/i)).toBeInTheDocument();
		// The pick is still allowed — warned, not blocked.
		expect(screen.getByRole('button', { name: 'Use this credential' })).toBeEnabled();

		// Committing the pick must not flip the warning into a green success:
		// stepping back to the credential step shows the adopted-state panel,
		// which keeps the never-connected wording (the warning stays visible
		// after it becomes binding).
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await user.click(await screen.findByRole('button', { name: /Back/ }));
		expect(await screen.findByText(/it was never connected/i)).toBeInTheDocument();
		expect(screen.queryByText(/reused as-is/i)).not.toBeInTheDocument();
	});

	it('offers a retry instead of silently collapsing when the toolkit list fails', async () => {
		// The nudge may be telling the operator to adopt — a failed fetch must
		// say so and offer a way out, not silently hide the picker.
		const request = planRequest();
		stubDirectoryAndRequest(request);
		let failures = 0;
		worker.use(
			http.get('/toolkits', () => {
				failures += 1;
				if (failures === 1) return new HttpResponse(null, { status: 500 });
				return HttpResponse.json({
					data: [
						{
							toolkit_id: 'tk_existing',
							name: 'Ops toolkit',
							description: null,
							active: true,
							created_by: 'usr_admin',
							created_at: '2026-01-01T00:00:00Z',
							updated_at: null,
							credential_count: 1,
							key_count: 0,
						},
					],
					has_more: false,
					next_cursor: null,
				});
			}),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		expect(
			await screen.findByText(/couldn.t load your existing toolkits/i),
		).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Retry' }));
		expect(await screen.findByLabelText(/use an existing toolkit/i)).toBeInTheDocument();
	});
});

describe('ProvisioningRequestDialog — already-in-place hints (#826)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	/** A composite whose chain-1 toolkit:bind and scope extra are already satisfied. */
	function satisfiedComposite(): AccessRequest {
		const request = compositeRequest();
		return {
			...request,
			items: request.items.map((it) => {
				if (it.id === 'a4' || it.id === 's1') return { ...it, already_satisfied: true };
				return it;
			}),
		};
	}

	it('nudges towards adopting when the agent is already wired to the API', async () => {
		stubDirectoryAndRequest(satisfiedComposite());
		renderWithProviders(
			<ProvisioningRequestDialog open request={satisfiedComposite()} onClose={() => {}} />,
		);

		// The fresh GET carries `already_satisfied` on chain 1's toolkit:bind:
		// the toolkit step points at the existing wiring before the operator
		// mints a duplicate toolkit.
		expect(await screen.findByText(/already wired to a toolkit serving/i)).toBeInTheDocument();
	});

	it('marks satisfied chains and extras on the review step', async () => {
		const request = satisfiedComposite();
		stubDirectoryAndRequest(request);
		let created = 0;
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
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Walk both no-auth chains to reach review.
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByLabelText('Toolkit name');
		await user.click(screen.getByRole('button', { name: /Create toolkit/i }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Chain 1 carries the existing-binding note — the operator created a
		// NEW toolkit despite the detected wiring, so the note is honest about
		// binding it alongside the existing setup. The satisfied scope grant
		// is labelled as already in place.
		expect(
			await screen.findByText(/already has a toolkit wired for this API/i),
		).toBeInTheDocument();
		expect(screen.getByText(/alongside that existing setup/i)).toBeInTheDocument();
		expect(screen.getByText(/already in place — approving records it/i)).toBeInTheDocument();
	});
});
