import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import type { ReviewSession, VendorAuthCapabilities } from '@/shared/credentials/api/vendors-types';

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

	it('renders the configure step with vendor header + scope catalog (no agent picker)', async () => {
		renderWithProviders(
			<VendorConnectFlow mode="self" vendor={vendor} onBack={vi.fn()} onDone={vi.fn()} />,
		);
		// Vendor header comes from the ``vendor`` prop, so it's up
		// immediately; scope data hydrates from the capabilities query.
		expect(await screen.findByText('GitHub')).toBeInTheDocument();
		expect(await screen.findByText('repo')).toBeInTheDocument();
		expect(await screen.findByText('read:user')).toBeInTheDocument();
		// Agent picker is intentionally NOT rendered — credentials still
		// bind through toolkits, so surfacing the choice would suggest
		// something the UI can't actually deliver on today. Comes back
		// once agent-credential bindings replace toolkit membership.
		expect(screen.queryByLabelText(/which agent uses this/i)).toBeNull();
	});

	it('start-and-confirm transitions to the awaiting step with a device_code challenge', async () => {
		// Stub the two-call cycle the useStartAndConfirmVendorConnect
		// mutation makes: :connect first, then :confirm. The confirm
		// response's ``kind`` drives the awaiting UI branch.
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

		// Wait for capabilities to hydrate — the button reads
		// disabled=(selectedScopes.size === 0), and selectedScopes is
		// seeded from the default scopes on ``capabilities.data``.
		// Clicking before those land leaves it disabled and the
		// mutation never fires. Awaiting a scope row proves the seed
		// has run.
		await screen.findByText('read:user');
		// Continue on the scopes page moves to the rules page (client-side
		// transition, no backend call). Second Continue on the rules page
		// fires the combined ``:connect`` + ``:confirm`` mutation and lands
		// us on the awaiting step.
		await user.click(await screen.findByRole('button', { name: /^continue$/i }));
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

	it('renders an error alert when the approval link is invalid', async () => {
		worker.use(
			http.get('/connect-sessions/sess_bad', () =>
				HttpResponse.json({ detail: 'session not found' }, { status: 404 }),
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
		// A 404 must NOT crash the review UI — it surfaces as an error
		// alert with a Close button so the human isn't left on a
		// half-rendered dialog with no way out.
		await screen.findByRole('button', { name: /close/i });
	});
});
