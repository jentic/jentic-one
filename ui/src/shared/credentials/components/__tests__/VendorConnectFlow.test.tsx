import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import {
	getConnectSession,
	startIntegrationConnect,
} from '@/shared/credentials/api/vendors-client';
import {
	getMockConnectSessions,
	resetConnectSessionsStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import type {
	ConfirmRequest,
	PermissionRule,
	ReviewSession,
	VendorAuthCapabilities,
} from '@/shared/credentials/api/vendors-types';

/**
 * VendorConnectFlow is the credentials dialog's vendor-approval sub-flow,
 * with two entry modes:
 *
 *   * ``self`` — the user picks an agent + scopes and runs start-and-confirm
 *     in one shot; the awaiting step then shows the vendor challenge until
 *     polling reports a terminal state.
 *   * ``approve`` — an agent already started the session and passed its
 *     owner an approval URL; the human lands here with just the session id
 *     + poll token and reviews what the agent asked for before confirming.
 *
 * These tests focus on the load-bearing bits: the configure step in ``self``
 * mode surfaces the vendor catalog + agent picker; a start-and-confirm
 * cycle drives to the awaiting step; ``approve`` mode fetches session
 * review data; and the terminal step surfaces both success and failure
 * outcomes.
 */

const vendor = {
	key: 'github',
	vendor: 'github.com',
	display_name: 'GitHub',
	flow_kinds: ['device_authorization'],
};

function stubCapabilities(caps: Partial<VendorAuthCapabilities> = {}): void {
	worker.use(
		http.get('/vendors/:key/auth-capabilities', () =>
			HttpResponse.json({
				vendor: 'github.com',
				display_name: 'GitHub',
				flows: [{ kind: 'device_authorization' }],
				scopes: [
					{
						name: 'repo',
						classification: 'write',
						default: false,
						description: 'Full control of private repositories',
					},
					{
						name: 'read:user',
						classification: 'read',
						default: true,
						description: 'Read basic user profile info',
					},
				],
				...caps,
			}),
		),
	);
}

function stubAgents(): void {
	worker.use(
		http.get('/agents', () =>
			HttpResponse.json({
				data: [
					{
						id: 'agnt_1',
						name: 'Scout',
						description: 'The team scout',
						actor_type: 'agent',
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

describe('VendorConnectFlow — self mode', () => {
	beforeEach(() => {
		stubCapabilities();
		stubAgents();
		// The awaiting step's device-code panel offers an "open vendor"
		// button that fires window.open; spy so we can assert without
		// pop-ups during tests.
		vi.spyOn(window, 'open').mockReturnValue(null);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('renders the configure step with vendor header + scope catalog + agent picker', async () => {
		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		// Vendor header comes from the ``vendor`` prop, so it's up
		// immediately; scope data hydrates from the capabilities query.
		expect(await screen.findByText('GitHub')).toBeInTheDocument();
		expect(await screen.findByText('repo')).toBeInTheDocument();
		expect(await screen.findByText('read:user')).toBeInTheDocument();
		// Agent picker IS rendered — theme-5 landed on main and
		// credentials now bind directly to a specified agent at
		// ``:confirm``, so the choice has to be surfaced here. Uses the
		// agent's ``name`` in the options.
		const picker = await screen.findByLabelText(/which agent uses this/i);
		expect(picker).toBeInTheDocument();
		expect(await screen.findByRole('option', { name: 'Scout' })).toBeInTheDocument();
	});

	it('disables the agent picker when preselectedAgentId is provided (agent-page entry)', async () => {
		renderWithProviders(
			<VendorConnectFlow
				mode="self"
				vendor={vendor}
				preselectedAgentId="agnt_1"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		// The dropdown renders but is disabled — locked to the agent
		// whose page opened the flow, so the user can't accidentally
		// re-target a binding during the connect step.
		const picker = (await screen.findByLabelText(
			/which agent uses this/i,
		)) as HTMLSelectElement;
		expect(picker).toBeDisabled();
		expect(picker.value).toBe('agnt_1');
	});

	it('connect-on-mount, then confirm on rules-continue, lands on awaiting', async () => {
		// Post-refactor: ``:connect`` fires at mount (so the session +
		// import exist before the user reaches the rules page), and
		// ``:confirm`` fires when the user continues off the rules page.
		// Self and approve flows now share the same shape from mount onward.
		worker.use(
			http.post('/integrations:connect', () =>
				HttpResponse.json(
					{
						session_id: 'sess_1',
						approval_url:
							'https://example.com/app/credentials?approve=sess_1&poll_token=tok',
						poll_token: 'tok',
						resolved_flow: 'device_authorization',
					},
					{ status: 201 },
				),
			),
			// ``:connect`` returning triggers a ``GET /connect-sessions/{id}``
			// (useConnectSession) so the rules page can read ``api_reference``.
			http.get('/connect-sessions/sess_1', () =>
				HttpResponse.json({
					session_id: 'sess_1',
					state: 'created',
					vendor_key: 'github',
					vendor_display_name: 'GitHub',
					resolved_flow: 'device_authorization',
					reason: null,
					requested_by_actor_id: 'usr_alice',
					scopes: [],
					requested_permission_rules: [],
					api_reference: { vendor: 'github-com', name: 'github-com', version: null },
				}),
			),
			http.post('/connect-sessions/sess_1\\:confirm', () =>
				HttpResponse.json({
					kind: 'device_authorization',
					user_code: 'ABCD-1234',
					verification_uri: 'https://github.com/login/device',
					verification_uri_complete: null,
					poll_interval_seconds: 5,
				}),
			),
			http.get('/connect-sessions/:id/status', () =>
				HttpResponse.json({
					status: 'polling',
					connected_as: null,
					credential_id: null,
					bound_scopes: null,
					error_code: null,
				}),
			),
		);

		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		const user = userEvent.setup();

		// Wait for both capabilities + session-id + agent choice (Continue
		// is gated on all three). ``read:user`` proves the scope catalog
		// hydrated; picking Scout satisfies the agent gate; the button
		// un-disables when ``:connect`` also returns.
		await screen.findByText('read:user');
		const picker = (await screen.findByLabelText(
			/which agent uses this/i,
		)) as HTMLSelectElement;
		await user.selectOptions(picker, 'agnt_1');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByText(/permission rules/i)).toBeInTheDocument();
		await user.click(
			await screen.findByRole('button', { name: /(skip.*continue|^continue$)/i }),
		);

		// The awaiting step shows the vendor's ``user_code`` verbatim —
		// this is the string the human types into the vendor page, so a
		// regression here breaks the whole device-code UX.
		expect(await screen.findByText('ABCD-1234')).toBeInTheDocument();
	});
});

describe('VendorConnectFlow — approve mode', () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('fetches session review data and renders the approve card', async () => {
		// Approve mode is what an owner lands on when they follow the
		// approval URL an agent handed them — the review data is the
		// only source for what the human is being asked to consent to.
		const session: ReviewSession = {
			session_id: 'sess_9',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control of private repositories',
				},
			],
			reason: 'Need repo push access to open a follow-up PR on issue #42.',
			requested_permission_rules: [],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		worker.use(
			http.get('/connect-sessions/sess_9', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({
					data: [
						{
							id: 'agnt_1',
							name: 'Scout',
							description: 'The scout',
							actor_type: 'agent',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_9"
				pollToken="tok9"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		// The header shows the vendor + subtitle that flags "an agent is
		// asking to connect on your behalf" — the crucial UX signal
		// that this is a human-in-the-loop review, not a self-initiated
		// action.
		await waitFor(() =>
			expect(screen.getByText(/an agent is asking to connect/i)).toBeInTheDocument(),
		);
		// The agent-requested scope MUST be surfaced with the "requested"
		// tag so the human notices what the agent added on top of the
		// vendor defaults.
		expect(await screen.findByText('repo')).toBeInTheDocument();
		expect(await screen.findByText('requested')).toBeInTheDocument();
		// The agent-supplied ``reason`` is the review page's single piece
		// of "why" context — a regression that dropped it would strip the
		// approver of the justification the whole review UX exists for.
		expect(
			await screen.findByText('Need repo push access to open a follow-up PR on issue #42.'),
		).toBeInTheDocument();
	});

	it('renders agent-requested rules on the rules page with a "requested" tag', async () => {
		// The agent's rules ride on ``ReviewSession.requested_permission_rules``;
		// after the human clicks Continue on scopes, the rules page pre-fills
		// with those rows and tags them so the owner can tell what the agent
		// asked for vs what they've since edited.
		const session: ReviewSession = {
			session_id: 'sess_rules',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			requested_permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/repos', match_mode: 'prefix' },
			],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		worker.use(
			http.get('/connect-sessions/sess_rules', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_rules"
				pollToken="tok_r"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		// Wait for the scope row to render — the seed effect populates
		// ``selectedScopes`` from ``requested`` scopes at that point, so
		// the Continue button becomes enabled.
		expect(await screen.findByText('repo')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		// The rules page mounts, agent-requested row is present with the tag.
		expect(await screen.findByText('Permission rules')).toBeInTheDocument();
		expect(await screen.findByText('requested by agent')).toBeInTheDocument();
	});

	it('lets the user add a custom rule via the inline form and rejects condition-less allows', async () => {
		// The rules-editor form mirrors ``PermissionRuleSchema._reject_condition_less_allow``
		// client-side: an ``allow`` rule with no methods and no path is
		// refused inline rather than 422'd by the server. Once we
		// constrain something, Save succeeds.
		const session: ReviewSession = {
			session_id: 'sess_edit',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			requested_permission_rules: [],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		worker.use(
			http.get('/connect-sessions/sess_edit', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_edit"
				pollToken="tok_e"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		expect(await screen.findByText('repo')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByText('Permission rules')).toBeInTheDocument();
		// Open the add-rule form.
		await user.click(screen.getByRole('button', { name: /add rule/i }));
		// Click Save with no methods + no path — client-side validator refuses.
		await user.click(screen.getByRole('button', { name: /^add$/i }));
		expect(
			await screen.findByText(/must constrain at least one of methods or path/i),
		).toBeInTheDocument();
		// Constrain the rule with a path — Save succeeds and the row appears.
		const pathInput = screen.getByPlaceholderText('/repos');
		await user.type(pathInput, '/issues');
		await user.click(screen.getByRole('button', { name: /^add$/i }));
		// The rendered row prints ``<path> (<match_mode>)`` — match on the
		// combined text so we're robust against exact whitespace.
		expect(await screen.findByText(/\/issues\s*\(prefix\)/i)).toBeInTheDocument();
	});

	it('lets the user edit an existing rule inline', async () => {
		// Every user-authored row exposes a Pencil icon that swaps the
		// row in place for an inline editor pre-filled with the rule's
		// current values — same UX shape as the Add form so both paths
		// feel identical. Saving writes back to the same index.
		const session: ReviewSession = {
			session_id: 'sess_ed',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			// One agent-requested rule pre-populates the list so the row
			// is present without needing to open the Add form first.
			requested_permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/repos', match_mode: 'prefix' },
			],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		worker.use(
			http.get('/connect-sessions/sess_ed', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_ed"
				pollToken="tok_ed"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		expect(await screen.findByText('repo')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByText('Permission rules')).toBeInTheDocument();
		// The seeded row is present.
		expect(await screen.findByText(/\/repos\s*\(prefix\)/i)).toBeInTheDocument();
		// Click Edit on the row.
		await user.click(screen.getByRole('button', { name: /edit rule/i }));
		// Inline editor replaces the row with the rule pre-filled — the
		// path input carries `/repos`.
		const pathInput = await screen.findByDisplayValue('/repos');
		// Edit the path.
		await user.clear(pathInput);
		await user.type(pathInput, '/issues');
		await user.click(screen.getByRole('button', { name: /^save$/i }));
		// The row now shows the edited path; the old one is gone.
		expect(await screen.findByText(/\/issues\s*\(prefix\)/i)).toBeInTheDocument();
		expect(screen.queryByText(/\/repos\s*\(prefix\)/i)).toBeNull();
	});

	it('expands a partial-verdict op row to show one ALLOW + one DENY sample', async () => {
		// A narrow rule (only allows jentic/jentic-one) matched against a
		// templated op like ``/repos/{owner}/{repo}/commits`` yields a
		// partial verdict — some concrete instances are allowed (that
		// specific owner/repo) and others denied. The leaf row starts
		// collapsed with a yellow "partial" pill; clicking it must show
		// exactly one allowed sample and one denied sample so the user
		// sees the actual slice covered.
		const session: ReviewSession = {
			session_id: 'sess_partial',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			// Narrow rule → the op will classify as ``partial``.
			requested_permission_rules: [
				{
					effect: 'allow',
					methods: ['GET'],
					path: '/repos/jentic/jentic-one',
					match_mode: 'prefix',
				},
			],
			api_reference: { vendor: 'github-com', name: 'github-com', version: '1.0.0' },
		};
		worker.use(
			http.get('/connect-sessions/sess_partial', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.get('/apis/github-com/github-com/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'repos/list-commits',
							method: 'GET',
							path: '/repos/{owner}/{repo}/commits',
							name: 'List commits',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_partial"
				pollToken="tok_p"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await screen.findByText('repo');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		// Open the top-level ``/repos/`` group.
		const groupHeader = await screen.findByRole('button', { name: /\/repos\// });
		await user.click(groupHeader);
		// The leaf op is present with the ``partial`` pill.
		const opText = await screen.findByText('/repos/{owner}/{repo}/commits');
		const opRow = opText.closest('div.bg-muted\\/20') as HTMLElement;
		expect(opRow).not.toBeNull();
		expect(within(opRow).getByText(/^partial$/i)).toBeInTheDocument();
		// Sample lines are hidden by default — the row is collapsed.
		expect(within(opRow).queryByText(/e\.g\./)).toBeNull();
		// Click the row to expand; one allowed + one denied sample appear.
		await user.click(opRow);
		const expanded = await screen.findAllByText(/e\.g\./);
		expect(expanded.length).toBeGreaterThanOrEqual(2);
		// Allowed sample carries the rule's owner + repo values.
		expect(
			await within(opRow).findByText(/\/repos\/jentic\/jentic-one\/commits/),
		).toBeInTheDocument();
	});

	it('flags a rule that touches no imported operation as "no ops affected"', async () => {
		// A syntactically-valid rule authored against a path the vendor
		// doesn't expose is a silent-fail: at runtime nothing matches
		// so no grant is created. The rule row surfaces "no ops affected"
		// once the ops list has finished loading (gate keeps the warning
		// from flashing while the import is still queued).
		const session: ReviewSession = {
			session_id: 'sess_noops',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			requested_permission_rules: [
				// Rule path does not correspond to any op below.
				{
					effect: 'allow',
					methods: ['GET'],
					path: '/nonexistent-surface',
					match_mode: 'prefix',
				},
			],
			api_reference: { vendor: 'github-com', name: 'github-com', version: '1.0.0' },
		};
		worker.use(
			http.get('/connect-sessions/sess_noops', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.get('/apis/github-com/github-com/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'repos/get',
							method: 'GET',
							path: '/repos/{owner}/{repo}',
							name: 'Get a repository',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_noops"
				pollToken="tok_noops"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await screen.findByText('repo');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		// The warning appears once the ops query has resolved.
		expect(await screen.findByText(/no ops affected/i)).toBeInTheDocument();
	});

	it('completes a path via the custom autocomplete dropdown (arrow + Enter)', async () => {
		// Custom autocomplete replaces the browser ``<datalist>``: capped
		// at 5 rows, styled with app tokens, keeps filtering as the user
		// types. Keyboard navigation (ArrowDown + Enter) must commit the
		// highlighted suggestion — the primary "no mouse" path.
		const session: ReviewSession = {
			session_id: 'sess_ac',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			requested_permission_rules: [],
			api_reference: { vendor: 'github-com', name: 'github-com', version: '1.0.0' },
		};
		worker.use(
			http.get('/connect-sessions/sess_ac', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.get('/apis/github-com/github-com/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{ operation_id: 'a', method: 'GET', path: '/repos/{owner}/{repo}' },
						{ operation_id: 'b', method: 'GET', path: '/repos/{owner}/{repo}/pulls' },
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_ac"
				pollToken="tok_ac"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await screen.findByText('repo');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		// Open the Add-rule form.
		await user.click(await screen.findByRole('button', { name: /add rule/i }));
		const pathInput = screen.getByPlaceholderText('/repos') as HTMLInputElement;
		// Focus opens the dropdown; type ``/r`` to start filtering (the
		// mocked op list gives suggestions starting with ``/repos``).
		await user.click(pathInput);
		await user.type(pathInput, '/r');
		// Wait for the listbox to render.
		await screen.findByRole('listbox');
		// ArrowDown moves the highlight; Enter commits it. The value
		// commits verbatim (one of the two op paths — either is fine, we
		// just verify the input has been replaced by a real suggestion).
		await user.keyboard('{ArrowDown}{Enter}');
		expect(pathInput.value.startsWith('/repos')).toBe(true);
		expect(pathInput.value.length).toBeGreaterThan('/r'.length);
	});

	it('renders an error alert when the approval link is invalid (403)', async () => {
		// New wire contract: missing session and poll_token mismatch BOTH
		// come back 403 problem+json (no session-id enumeration oracle) —
		// what a 404 used to signal.
		worker.use(
			http.get('/connect-sessions/sess_bad', () =>
				HttpResponse.json(
					{
						type: 'about:blank',
						title: 'Forbidden',
						detail: 'Unknown session or invalid poll token.',
						status: 403,
					},
					{ status: 403 },
				),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_bad"
				pollToken="tok"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		// A 403 must NOT crash the review UI — it surfaces as an error
		// alert with a Close button so the human isn't left on a
		// half-rendered dialog with no way out.
		await screen.findByRole('button', { name: /close/i });
	});
});

/**
 * Regression suite for the connect-wizard review findings: the poll_token
 * wire adaptation (review GET / :confirm are token-gated), the "Try again"
 * dead end (H1), scope-less vendors (H2), the operations/comment
 * round-trip in the rules editor (H3), and the no-agents dead end (M2).
 * The first test drives the wizard end to end through the credentials
 * module's OWN MSW handlers — no per-test endpoint stubs — so the whole
 * mocked flow (connect → review → confirm → device code) is exercised
 * exactly as mocked dev serves it.
 */
describe('VendorConnectFlow — connect-wizard regressions', () => {
	beforeEach(() => {
		resetConnectSessionsStore();
		resetCredentialsStore();
		stubAgents();
		vi.spyOn(window, 'open').mockReturnValue(null);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetConnectSessionsStore();
		resetCredentialsStore();
	});

	it('drives the wizard end to end through the module MSW handlers (scope-less vendor, no agent picked)', async () => {
		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		const user = userEvent.setup();

		// The default capabilities handler advertises no scopes — the
		// empty-state copy promises the vendor's defaults will be used, so
		// Continue must NOT stay dead (H2).
		expect(await screen.findByText(/doesn't expose any scopes/i)).toBeInTheDocument();
		// No agent is selected either (the picker defaults to "no agent") —
		// agent_id is optional at :confirm (M2).
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));

		expect(await screen.findByText(/permission rules/i)).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: /skip.*continue/i }));

		// The device challenge from the module's :confirm handler renders —
		// which also proves the poll_token rode the query string (the
		// handler 403s without it).
		expect(await screen.findByText('MOCK-1234')).toBeInTheDocument();
		const sessions = getMockConnectSessions();
		expect(sessions).toHaveLength(1);
		expect(sessions[0].confirmBodies).toHaveLength(1);
		expect(sessions[0].confirmBodies[0].agent_id ?? null).toBeNull();
	});

	it('review GET requires the poll_token: 403 with a wrong token, 200 with the right one', async () => {
		const started = await startIntegrationConnect({ vendor: 'github' });
		await expect(getConnectSession(started.session_id, 'wrong-token')).rejects.toMatchObject({
			status: 403,
		});
		await expect(getConnectSession('sess_unknown', started.poll_token)).rejects.toMatchObject({
			status: 403,
		});
		const review = await getConnectSession(started.session_id, started.poll_token);
		expect(review.session_id).toBe(started.session_id);
	});

	it('"Try again" after a failed sign-in re-fires :connect and re-enables Continue (H1)', async () => {
		stubCapabilities();
		let connectCalls = 0;
		worker.use(
			http.post('/integrations:connect', () => {
				connectCalls += 1;
				return HttpResponse.json(
					{
						session_id: `sess_retry_${connectCalls}`,
						approval_url: `/app/credentials?approve=sess_retry_${connectCalls}&poll_token=tok_retry_${connectCalls}`,
						poll_token: `tok_retry_${connectCalls}`,
						resolved_flow: 'device_authorization',
					},
					{ status: 201 },
				);
			}),
			http.get('/connect-sessions/:id', ({ params }) =>
				HttpResponse.json({
					session_id: String(params.id),
					state: 'created',
					vendor_key: 'github',
					vendor_display_name: 'GitHub',
					resolved_flow: 'device_authorization',
					reason: null,
					requested_by_actor_id: 'usr_alice',
					scopes: [],
					requested_permission_rules: [],
					api_reference: { vendor: 'github-com', name: 'github-com', version: null },
				}),
			),
			http.post('/connect-sessions/:id\\:confirm', () =>
				HttpResponse.json({
					kind: 'device_authorization',
					user_code: 'FAIL-0001',
					verification_uri: 'https://github.com/login/device',
					verification_uri_complete: null,
					poll_interval_seconds: 1,
				}),
			),
			// Every poll reports a terminal failure, driving the wizard to
			// the terminal step with the "Try again" affordance.
			http.get('/connect-sessions/:id/status', () =>
				HttpResponse.json({
					status: 'failed',
					connected_as: null,
					credential_id: null,
					bound_scopes: null,
					error_code: 'access_denied',
				}),
			),
		);

		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		const user = userEvent.setup();

		await screen.findByText('read:user');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.click(await screen.findByRole('button', { name: /skip.*continue/i }));

		// Polling reports `failed` → terminal step with Try again.
		expect(await screen.findByText(/sign-in failed/i)).toBeInTheDocument();
		expect(connectCalls).toBe(1);
		await user.click(screen.getByRole('button', { name: /try again/i }));

		// Back on the configure step, and a SECOND :connect fired — the old
		// behaviour left Continue permanently disabled because the mount
		// effect never re-ran and session stayed null.
		expect(await screen.findByText('read:user')).toBeInTheDocument();
		await waitFor(() => expect(connectCalls).toBe(2));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
	});

	it('the no-agents empty state links to the Agents page and does not block Continue (M2)', async () => {
		stubCapabilities();
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);

		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		const user = userEvent.setup();

		// The empty state explains connect-now-bind-later and links out.
		expect(await screen.findByText(/connect without one/i)).toBeInTheDocument();
		const agentsLink = screen.getByRole('link', { name: /create an agent/i });
		// Basename-relative href — the router basename (/app) is applied at
		// render time by the real app shell.
		expect(agentsLink.getAttribute('href')).toMatch(/\/agents$/);

		// Continue enables once :connect returns — no agent required.
		await screen.findByText('read:user');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByText(/permission rules/i)).toBeInTheDocument();
	});

	it("preserves an agent-requested rule's operations + comment through an edit and on the wire (H3)", async () => {
		const requestedRule: PermissionRule = {
			effect: 'allow',
			methods: ['GET'],
			path: '/repos',
			match_mode: 'prefix',
			operations: ['repos/get'],
			comment: 'Read-only repo metadata',
		};
		const session: ReviewSession = {
			session_id: 'sess_ops',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [
				{
					name: 'repo',
					classification: 'write',
					default: false,
					requested: true,
					description: 'Full control',
				},
			],
			reason: null,
			requested_permission_rules: [requestedRule],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		let confirmBody: ConfirmRequest | null = null;
		let confirmToken: string | null = null;
		worker.use(
			http.get('/connect-sessions/sess_ops', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.post('/connect-sessions/sess_ops\\:confirm', async ({ request }) => {
				confirmToken = new URL(request.url).searchParams.get('poll_token');
				confirmBody = (await request.json()) as ConfirmRequest;
				return HttpResponse.json({
					kind: 'device_authorization',
					user_code: 'OPSX-1234',
					verification_uri: 'https://github.com/login/device',
					verification_uri_complete: null,
					poll_interval_seconds: 1,
				});
			}),
			http.get('/connect-sessions/sess_ops/status', () =>
				HttpResponse.json({
					status: 'polling',
					connected_as: null,
					credential_id: null,
					bound_scopes: null,
					error_code: null,
				}),
			),
		);

		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_ops"
				pollToken="tok_ops"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();

		expect(await screen.findByText('repo')).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /^continue$/i })).not.toBeDisabled(),
		);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByText('Permission rules')).toBeInTheDocument();

		// The operations constraint + comment are visible on the row — the
		// approver can see exactly what the agent asked for.
		expect(await screen.findByText('repos/get')).toBeInTheDocument();
		expect(screen.getByText('Read-only repo metadata')).toBeInTheDocument();

		// Edit the rule's path — the un-editable operations/comment must
		// survive the round-trip (the old draft dropped them, silently
		// WIDENING the grant).
		await user.click(screen.getByRole('button', { name: /edit rule/i }));
		const pathInput = await screen.findByDisplayValue('/repos');
		await user.clear(pathInput);
		await user.type(pathInput, '/issues');
		await user.click(screen.getByRole('button', { name: /^save$/i }));
		expect(await screen.findByText(/\/issues\s*\(prefix\)/i)).toBeInTheDocument();
		expect(screen.getByText('repos/get')).toBeInTheDocument();
		expect(screen.getByText('Read-only repo metadata')).toBeInTheDocument();

		// Confirm — the wire body still carries the constraint, and the
		// poll_token rode the query string.
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await waitFor(() => expect(confirmBody).not.toBeNull());
		expect(confirmToken).toBe('tok_ops');
		expect(confirmBody!.permission_rules[0]).toMatchObject({
			effect: 'allow',
			path: '/issues',
			match_mode: 'prefix',
			operations: ['repos/get'],
			comment: 'Read-only repo metadata',
		});
	});

	it('approve mode: a scope-less session can proceed to the rules page (H2)', async () => {
		const session: ReviewSession = {
			session_id: 'sess_noscopes',
			state: 'created',
			vendor_key: 'github',
			vendor_display_name: 'GitHub',
			resolved_flow: 'device_authorization',
			requested_by_actor_id: 'agnt_1',
			scopes: [],
			reason: null,
			requested_permission_rules: [],
			api_reference: { vendor: 'github-com', name: 'github-com', version: null },
		};
		worker.use(
			http.get('/connect-sessions/sess_noscopes', () => HttpResponse.json(session)),
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId="sess_noscopes"
				pollToken="tok_ns"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		expect(await screen.findByText(/doesn't expose any scopes/i)).toBeInTheDocument();
		const cont = screen.getByRole('button', { name: /^continue$/i });
		expect(cont).not.toBeDisabled();
		await user.click(cont);
		expect(await screen.findByText('Permission rules')).toBeInTheDocument();
	});
});
