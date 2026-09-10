import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
 * The 2-item provisioning flow (toolkits retired): a plan is
 * `credential:provision` (inert placeholder) + `credential:bind` (binds the
 * AGENT directly to the credential + rules). The wizard creates/adopts a
 * credential, amends `{item_id, resource_id}` onto the bind — never `to_id`,
 * which is gone from the amend schema — and approves everything in one decide.
 * These tests pin that no toolkit endpoint is ever touched.
 */

// The real CreateCredentialDialog is a heavy two-step picker/form; the wizard
// only cares about its `onCreated` callback. Stub it with a one-click
// stand-in so the create path can be exercised without driving the form.
vi.mock('@/shared/credentials/components/CreateCredentialDialog', () => ({
	CreateCredentialDialog: ({
		open,
		onCreated,
	}: {
		open: boolean;
		onCreated: (info: {
			credentialId: string;
			type: string;
			provider: string;
			needsConnect: boolean;
		}) => void;
	}) =>
		open ? (
			<button
				onClick={() =>
					onCreated({
						credentialId: 'cred_created_1',
						type: 'api_key',
						provider: 'manual',
						needsConnect: false,
					})
				}
			>
				Mock create credential
			</button>
		) : null,
}));

const AGENT_ID = 'agnt_wizard_test';

/** A no-auth provisioning plan (no manual credential step). */
function planRequest(): AccessRequest {
	const ref = { vendor: 'open-meteo-com', name: 'forecast' };
	return {
		id: 'arq_plan_noauth',
		actor_id: AGENT_ID,
		status: 'pending',
		requested_by: AGENT_ID,
		created_by: AGENT_ID,
		approve_url: 'https://app.example.test/access-requests/arq_plan_noauth',
		reason: 'need weather data',
		filed_at: new Date().toISOString(),
		expires_at: new Date(Date.now() + 3_600_000).toISOString(),
		items: [
			{
				id: 'i1',
				resource_type: 'credential',
				action: 'provision',
				status: 'pending',
				resource_reference: { ...ref, security_scheme: 'no_auth' },
			},
			{
				id: 'i2',
				resource_type: 'credential',
				action: 'bind',
				status: 'pending',
				resource_reference: ref,
				// The server substitutes a read-only default when the filer
				// omitted a policy, so pending binds always carry one.
				rules: [{ effect: 'allow', methods: ['GET'] }],
			},
		],
	};
}

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

/** Track any (retired) toolkit-endpoint traffic — must stay at zero. */
function trackToolkitCalls(): { count: () => number } {
	let calls = 0;
	worker.use(
		http.all('/toolkits', () => {
			calls += 1;
			return new HttpResponse(null, { status: 500 });
		}),
		http.all('/toolkits/*', () => {
			calls += 1;
			return new HttpResponse(null, { status: 500 });
		}),
	);
	return { count: () => calls };
}

/** Stub the amend/decide submit path, echoing decisions onto the request. */
function stubSubmitPath(
	request: AccessRequest,
	opts?: {
		onAmend?: (body: unknown) => void;
		onDecide?: (body: unknown) => void;
	},
) {
	worker.use(
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
}

type AmendItem = { item_id: string; resource_id?: string; to_id?: string; rules?: unknown[] };

describe('ProvisioningRequestDialog — no-auth plan (2-item chain)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	it('auto-creates the NO_AUTH credential and amends resource_id only — no toolkit calls', async () => {
		const request = planRequest();
		stubDirectoryAndRequest(request);
		const toolkits = trackToolkitCalls();
		let amendBody: unknown;
		let decideBody: unknown;
		let credentialCreateBody: unknown;
		worker.use(
			http.post('/credentials', async ({ request: httpReq }) => {
				credentialCreateBody = await httpReq.json();
				return HttpResponse.json({ credential: { credential_id: 'cred_noauth_1' } });
			}),
		);
		stubSubmitPath(request, {
			onAmend: (b) => (amendBody = b),
			onDecide: (b) => (decideBody = b),
		});
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// No-auth single chain: the wizard opens straight on the rules step.
		expect(await screen.findByText('Confirm what the agent can do')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /^Review/ }));

		// Review is explicit that the credential is auto-created.
		expect(await screen.findByText(/needs no auth/)).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// The NO_AUTH credential targets the plan's API.
		expect(credentialCreateBody).toMatchObject({
			type: 'no_auth',
			api: { vendor: 'open-meteo-com', name: 'forecast' },
		});

		// One amend: the bind gets the credential id + rules; the provision
		// placeholder is stamped with the same id (audit honesty). NOTHING
		// carries `to_id` — it is gone from the amend schema.
		const amendments = (amendBody as { items: AmendItem[] }).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('i2')?.resource_id).toBe('cred_noauth_1');
		expect(byItem.get('i2')?.rules).toEqual([
			{ effect: 'allow', methods: ['GET'], path: null, operations: null },
		]);
		expect(byItem.get('i1')?.resource_id).toBe('cred_noauth_1');
		expect(amendments.every((a) => !('to_id' in a))).toBe(true);

		// One decide approving both items; the toolkit surface was never touched.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		expect(decisions).toHaveLength(2);
		expect(decisions.every((d) => d.decision === 'approved')).toBe(true);
		expect(toolkits.count()).toBe(0);
	});

	it('resolves the agent name for the header via the directory-miss fallback', async () => {
		stubDirectoryAndRequest(planRequest(), { directoryMisses: true });
		renderWithProviders(
			<ProvisioningRequestDialog open request={planRequest()} onClose={() => {}} />,
		);
		// The header badge upgrades from the raw agnt_… id to the fetched name.
		expect(await screen.findByText('Weather Agent')).toBeInTheDocument();
	});
});

describe('ProvisioningRequestDialog — operator-created credential (auth plan)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	function stubEmptyCredentialList() {
		worker.use(
			http.get('/credentials', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
	}

	it('creates a credential, then amends {item_id, resource_id} — never to_id', async () => {
		const request = authPlanRequest();
		stubDirectoryAndRequest(request);
		stubEmptyCredentialList();
		const toolkits = trackToolkitCalls();
		let amendBody: unknown;
		stubSubmitPath(request, { onAmend: (b) => (amendBody = b) });
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Auth chain: the credential step comes first, pre-scoped to the API.
		expect(await screen.findByText('Connect a credential')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /Connect credential/ }));
		await user.click(await screen.findByRole('button', { name: 'Mock create credential' }));

		// Created → rules → review → approve.
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		const amendments = (amendBody as { items: AmendItem[] }).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('i2')?.resource_id).toBe('cred_created_1');
		expect(byItem.get('i1')?.resource_id).toBe('cred_created_1');
		expect(amendments.every((a) => !('to_id' in a))).toBe(true);
		expect(toolkits.count()).toBe(0);
	});

	it('asks in-dialog (never window.confirm) and discards the created credential on cancel', async () => {
		const request = authPlanRequest();
		stubDirectoryAndRequest(request);
		stubEmptyCredentialList();
		let deleted: string | null = null;
		worker.use(
			http.delete('/credentials/:id', ({ params }) => {
				deleted = String(params.id);
				return new HttpResponse(null, { status: 204 });
			}),
		);
		let closed = false;
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

		await screen.findByText('Connect a credential');
		await user.click(screen.getByRole('button', { name: /Connect credential/ }));
		await user.click(await screen.findByRole('button', { name: 'Mock create credential' }));
		await screen.findByRole('button', { name: /^Review/ });

		// Cancel the wizard mid-fulfilment → the in-dialog confirmation appears.
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(await screen.findByText('Keep this setup for later?')).toBeInTheDocument();
		expect(closed).toBe(false);

		await user.click(screen.getByRole('button', { name: /Discard/ }));
		await waitFor(() => expect(closed).toBe(true));
		expect(deleted).toBe('cred_created_1');
	});

	it('"Keep & finish later" keeps the credential and the reopened draft resumes it', async () => {
		const request = authPlanRequest();
		stubDirectoryAndRequest(request);
		stubEmptyCredentialList();
		let deleteCalls = 0;
		worker.use(
			http.delete('/credentials/:id', () => {
				deleteCalls += 1;
				return new HttpResponse(null, { status: 204 });
			}),
		);
		const user = userEvent.setup();

		// First session: create the credential, then "Keep & finish later". The
		// production mount path (AccessRequestDecisionDialog) UNMOUNTS the
		// wizard on close, so we simulate that with a full unmount.
		const first = renderWithProviders(
			<ProvisioningRequestDialog open request={authPlanRequest()} onClose={() => {}} />,
		);
		await screen.findByText('Connect a credential');
		await user.click(screen.getByRole('button', { name: /Connect credential/ }));
		await user.click(await screen.findByRole('button', { name: 'Mock create credential' }));
		await screen.findByRole('button', { name: /^Review/ });
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await user.click(await screen.findByRole('button', { name: /Keep & finish later/ }));
		expect(deleteCalls).toBe(0);
		first.unmount();

		// Second session for the SAME request: the draft must restore the rules
		// step with the existing credential — never re-run the create step,
		// which would strand cred_created_1 and accumulate a second credential.
		renderWithProviders(
			<ProvisioningRequestDialog open request={authPlanRequest()} onClose={() => {}} />,
		);
		expect(await screen.findByRole('button', { name: /^Review/ })).toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: /Connect credential/ }),
		).not.toBeInTheDocument();
	});
});

/** A composite: two no-auth chains + a scope grant, as the CLI now files it. */
function compositeRequest(): AccessRequest {
	const refA = { vendor: 'open-meteo-com', name: 'forecast' };
	const refB = { vendor: 'country-is', name: 'country-is' };
	const chain = (ref: { vendor: string; name: string }, p: string) => [
		{
			id: `${p}1`,
			resource_type: 'credential',
			action: 'provision',
			status: 'pending',
			resource_reference: { ...ref, security_scheme: 'no_auth' },
		},
		{
			id: `${p}2`,
			resource_type: 'credential',
			action: 'bind',
			status: 'pending',
			resource_reference: ref,
			rules: [{ effect: 'allow', methods: ['GET'] }],
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
			// Both chains are no-auth: the submit path auto-creates a NO_AUTH
			// credential per fulfilled chain.
			http.post('/credentials', () => {
				created += 1;
				return HttpResponse.json({
					credential: { credential_id: `cred_noauth_${created}` },
				});
			}),
		);
		stubSubmitPath(request, opts);
		return request;
	}

	it('walks both chains, then approves everything in one amend + one decide', async () => {
		let amendBody: unknown;
		let decideBody: unknown;
		const request = stubComposite({
			onAmend: (b) => (amendBody = b),
			onDecide: (b) => (decideBody = b),
		});
		const toolkits = trackToolkitCalls();
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Chain 1 (no-auth): rules → "Next API". Chain 2: rules → Review.
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Review lists both chains and the extra scope grant. (The APIs also
		// appear as subtitle badges, so assert on multiplicity, not uniqueness.)
		expect((await screen.findAllByText('open-meteo-com/forecast')).length).toBeGreaterThan(1);
		expect(screen.getAllByText('country-is/country-is').length).toBeGreaterThan(1);
		expect(screen.getByText(/scope catalog:import/)).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// One amend carrying BOTH chains' bind items, each keyed to its own
		// credential — never cross-wired. The inert placeholders are stamped
		// with the ids that fulfilled them (audit honesty, #897).
		const amendments = (amendBody as { items: AmendItem[] }).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('a2')?.resource_id).toMatch(/^cred_noauth_/);
		expect(byItem.get('b2')?.resource_id).toMatch(/^cred_noauth_/);
		expect(byItem.get('a2')?.resource_id).not.toBe(byItem.get('b2')?.resource_id);
		expect(byItem.get('a1')?.resource_id).toBe(byItem.get('a2')?.resource_id);
		expect(byItem.get('b1')?.resource_id).toBe(byItem.get('b2')?.resource_id);
		expect(amendments.every((a) => !('to_id' in a))).toBe(true);

		// One decide approving every pending item, the scope grant included.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		expect(decisions).toHaveLength(5);
		expect(decisions.every((d) => d.decision === 'approved')).toBe(true);
		expect(toolkits.count()).toBe(0);
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
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(screen.getByRole('button', { name: /Skip this API/ }));

		// Review flags the skipped chain and still allows submitting.
		expect(await screen.findByText(/skipped — will be denied/)).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// Only chain 1 was amended (its placeholder included — never the
		// skipped chain's)…
		const amendments = (amendBody as { items: AmendItem[] }).items;
		expect(amendments.map((a) => a.item_id).sort()).toEqual(['a1', 'a2']);
		// …and the decide denies exactly chain 2's two items.
		const decisions = (decideBody as { items: { item_id: string; decision: string }[] }).items;
		const denied = decisions.filter((d) => d.decision === 'denied').map((d) => d.item_id);
		expect(denied.sort()).toEqual(['b1', 'b2']);
		expect(decisions.filter((d) => d.decision === 'approved')).toHaveLength(3);
	});

	it('backing into a skipped chain lets the operator include it again', async () => {
		let decideBody: unknown;
		const request = stubComposite({ onDecide: (b) => (decideBody = b) });
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Skip chain 2, then change your mind from review.
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(screen.getByRole('button', { name: /Skip this API/ }));
		await screen.findByText(/skipped — will be denied/);
		await user.click(screen.getByRole('button', { name: /Back/ }));

		// The skipped chain's step offers the un-skip affordance; taking it
		// restores the normal flow, and fulfilment proceeds as usual.
		await user.click(await screen.findByRole('button', { name: /Include this API/ }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		expect(screen.queryByText(/skipped — will be denied/)).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// Nothing is denied — the un-skipped chain was fulfilled and approved.
		const decisions = (decideBody as { items: { decision: string }[] }).items;
		expect(decisions).toHaveLength(5);
		expect(decisions.every((d) => d.decision === 'approved')).toBe(true);
	});

	it('persists the draft to sessionStorage so a same-tab redirect can resume', async () => {
		const request = stubComposite();
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Touch the wizard (skipping mutates chain state) so the draft persists.
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(screen.getByRole('button', { name: /Skip this API/ }));

		// The draft must be in sessionStorage — a module-scoped map would not
		// survive the OAuth popup-blocked same-tab redirect fallback. (The
		// persist effect is passive; wait for it to flush.)
		await waitFor(() => {
			expect(sessionStorage.getItem('jentic.provisioningWizardDrafts.v2')).not.toBeNull();
		});
		const raw = sessionStorage.getItem('jentic.provisioningWizardDrafts.v2');
		const stored = JSON.parse(raw!) as Record<
			string,
			{ chains: { key: string; skipped: boolean }[] }
		>;
		const draft = stored[request.id];
		expect(draft).toBeDefined();
		expect(draft.chains[0].key).toContain('open-meteo-com');
		expect(draft.chains[0].skipped).toBe(true);
	});
});

describe('ProvisioningRequestDialog — adopt existing credentials (#826)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	/** Stub the picker's list endpoint + the amend/decide submit path. */
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
		);
		stubSubmitPath(request, opts);
	}

	it('adopting an existing credential skips the connect flow and amends its id', async () => {
		let amendBody: unknown;
		const request = authPlanRequest();
		stubAdoption(request, { onAmend: (b) => (amendBody = b) });
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// The credential step offers the vendor-scoped existing credentials;
		// staging one and committing advances straight to rules — no create
		// form, no connect flow.
		const picker = await screen.findByLabelText(/use an existing credential/i);
		// No satisfaction hint on this request — the nudge must not render.
		expect(screen.queryByText(/already wired/i)).not.toBeInTheDocument();
		await user.selectOptions(picker, 'cred_exist');
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Review names the adopted credential and marks it as pre-existing.
		expect(await screen.findByText('Weather key')).toBeInTheDocument();
		expect(screen.getByText('(existing)')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		// The bind was amended to the ADOPTED id; the credential:provision
		// placeholder records the reused credential (#897 audit honesty).
		const amendments = (amendBody as { items: AmendItem[] }).items;
		const byItem = new Map(amendments.map((a) => [a.item_id, a]));
		expect(byItem.get('i2')?.resource_id).toBe('cred_exist');
		expect(byItem.get('i1')?.resource_id).toBe('cred_exist');
	});

	it('never offers to discard adopted credentials on cancel', async () => {
		const request = authPlanRequest();
		stubAdoption(request);
		let closed = false;
		let deleteCalls = 0;
		worker.use(
			http.delete('/credentials/:id', () => {
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

		const picker = await screen.findByLabelText(/use an existing credential/i);
		await user.selectOptions(picker, 'cred_exist');
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await screen.findByRole('button', { name: /^Review/ });

		// Cancel: the wizard created NOTHING this session (the credential was
		// adopted), so there are no orphans — close directly, never offering
		// to delete infrastructure the operator set up outside the wizard.
		await user.click(screen.getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(closed).toBe(true));
		// The confirm <dialog> stays mounted while closed, so assert on
		// visibility rather than presence.
		expect(screen.getByText('Keep this setup for later?')).not.toBeVisible();
		expect(deleteCalls).toBe(0);
	});

	it('slugifies the raw filed vendor and hides inactive credentials in the picker', async () => {
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
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);

		const credPicker = await screen.findByLabelText(/use an existing credential/i);
		expect(queriedVendor).toBe('open-meteo-com');
		expect(within(credPicker).getByRole('option', { name: /Weather key/ })).toBeVisible();
		expect(
			within(credPicker).queryByRole('option', { name: /Old disabled key/ }),
		).not.toBeInTheDocument();
	});

	it('names the wired credential, floats it in the picker, and reviews honestly on adopt', async () => {
		// The backend hint carries WHICH credential satisfies the bind
		// (already_satisfied_by = a CREDENTIAL id now) — the nudge names it and
		// the picker floats it so the operator isn't left hunting through
		// name-only options.
		const base = authPlanRequest();
		const request = {
			...base,
			items: base.items.map((it) =>
				it.id === 'i2'
					? { ...it, already_satisfied: true, already_satisfied_by: 'cred_exist' }
					: it,
			),
		};
		stubAdoption(request);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		const nudge = await screen.findByText(/already wired to/i);
		expect(nudge).toHaveTextContent('Weather key');

		const picker = await screen.findByLabelText(/use an existing credential/i);
		const options = within(picker).getAllByRole('option');
		expect(options[1]).toHaveTextContent(/Weather key .* — already linked to this agent/);

		// Adopt it and reach review: the note is the adopted variant, honest
		// about the rules being updated (an approve REPLACES binding rules —
		// never "nothing changes").
		await user.selectOptions(picker, 'cred_exist');
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await user.click(await screen.findByRole('button', { name: /^Review/ }));
		expect(
			await screen.findByText(/reuses that setup and updates its permission rules/i),
		).toBeInTheDocument();
	});

	it('flags a never-connected OAuth credential and warns before adoption', async () => {
		// The adopt picker must not blindly trust the operator's choice: a
		// never-signed-in OAuth credential would only fail at execute time. The
		// redacted listing carries the derived connect state, so the picker
		// warns BEFORE the pick is committed (#890).
		const request = authPlanRequest();
		stubDirectoryAndRequest(request);
		stubSubmitPath(request);
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
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

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

	it('offers a retry instead of silently collapsing when the credential list fails', async () => {
		// The nudge may be telling the operator to adopt — a failed fetch must
		// say so and offer a way out, not silently hide the picker.
		const request = authPlanRequest();
		stubDirectoryAndRequest(request);
		let failures = 0;
		worker.use(
			http.get('/credentials', () => {
				failures += 1;
				if (failures === 1) return new HttpResponse(null, { status: 500 });
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
			await screen.findByText(/couldn.t load your existing credentials/i),
		).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Retry' }));
		expect(await screen.findByLabelText(/use an existing credential/i)).toBeInTheDocument();
	});
});

describe('ProvisioningRequestDialog — shared rule sets', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	it('shows the rule-set pointer read-only and amends without inline rules', async () => {
		// A bind carrying `rule_set_id` is governed by the shared set (mutually
		// exclusive with inline rules): the wizard must neither open the rule
		// editor nor send `rules` in the amendment — that would detach the set.
		const base = planRequest();
		const request = {
			...base,
			items: base.items.map((it) =>
				it.id === 'i2' ? { ...it, rules: null, rule_set_id: 'prs_shared_01' } : it,
			),
		};
		stubDirectoryAndRequest(request);
		let amendBody: unknown;
		worker.use(
			http.post('/credentials', () =>
				HttpResponse.json({ credential: { credential_id: 'cred_noauth_9' } }),
			),
		);
		stubSubmitPath(request, { onAmend: (b) => (amendBody = b) });
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// The rules step shows the shared-set panel instead of the editor.
		expect(await screen.findByText(/governed by a shared permission rule set/i)).toBeVisible();
		expect(screen.getByText('prs_shared_01')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /^Review/ }));
		expect(await screen.findByText(/shared rule set/)).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /Approve & grant access/ }));
		expect(await screen.findByText('Access granted')).toBeInTheDocument();

		const amendments = (amendBody as { items: AmendItem[] }).items;
		const bind = amendments.find((a) => a.item_id === 'i2')!;
		expect(bind.resource_id).toBe('cred_noauth_9');
		expect('rules' in bind).toBe(false);
		expect('to_id' in bind).toBe(false);
	});
});

describe('ProvisioningRequestDialog — already-in-place hints (#826)', () => {
	beforeEach(() => {
		setToken('test-token');
		resetProvisioningWizardDrafts();
	});
	afterEach(() => clearToken());

	it('marks satisfied chains and extras on the review step', async () => {
		// Chain 1's credential:bind and the scope extra are already satisfied.
		const request = {
			...compositeRequest(),
			items: compositeRequest().items.map((it) =>
				it.id === 'a2' || it.id === 's1' ? { ...it, already_satisfied: true } : it,
			),
		};
		stubDirectoryAndRequest(request);
		let created = 0;
		worker.use(
			http.post('/credentials', () => {
				created += 1;
				return HttpResponse.json({
					credential: { credential_id: `cred_noauth_${created}` },
				});
			}),
		);
		renderWithProviders(
			<ProvisioningRequestDialog open request={request} onClose={() => {}} />,
		);
		const user = userEvent.setup();

		// Walk both no-auth chains to reach review.
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /Next API/ }));
		await screen.findByText(/Confirm what the agent can do on/);
		await user.click(await screen.findByRole('button', { name: /^Review/ }));

		// Chain 1 carries the existing-binding note — the operator proceeds
		// with a NEW (auto-created) credential despite the detected wiring, so
		// the note is honest about binding it alongside the existing setup.
		// The satisfied scope grant is labelled as already in place.
		expect(
			await screen.findByText(/already has a credential wired for this API/i),
		).toBeInTheDocument();
		expect(screen.getByText(/alongside that existing setup/i)).toBeInTheDocument();
		expect(screen.getByText(/already in place — approving records it/i)).toBeInTheDocument();
	});
});
