import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import type { ReactElement } from 'react';
import { worker } from '@/mocks/browser';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import { listOpenConnectRequests } from '@/shared/credentials/api/vendors-client';
import {
	SharedAppSurfaceContext,
	type SharedAppSurface,
} from '@/shared/credentials/lib/sharedAppSurface';
import {
	getEndedMockConnectSessions,
	getMockConnectSessions,
	resetConnectSessionsStore,
	resetCredentialsStore,
	seedMockAgentConnectSession,
	type MockConnectSession,
} from '@/shared/credentials/mocks/handlers';
import type { ExistingCredential, PermissionRule } from '@/shared/credentials/api/vendors-types';

/** Choose how long the access lasts (7 days) when the step asks and nothing is chosen yet. */
async function pickDurationIfAsked(user: ReturnType<typeof userEvent.setup>): Promise<void> {
	for (const testId of ['approve-duration-preset', 'self-connect-duration-preset']) {
		const field = screen.queryByTestId(testId);
		if (field instanceof HTMLSelectElement && field.value === '') {
			await user.selectOptions(field, '7d');
		}
	}
}

/**
 * Approve mode for the connect-session kinds beyond a vendor OAuth sign-in:
 * `manual_*` sessions (the approver types the secret), `awaiting_app`
 * sessions (an OAuth API with no app yet), binding an existing credential,
 * and the "Not now" / "Reject" split. Driven through the module MSW handlers,
 * which model the backend's digest, rules and kind checks.
 */

const SUGGESTED_RULE: PermissionRule = {
	effect: 'allow',
	methods: ['GET'],
	path: '/v1/charges',
	match_mode: 'exact',
};

type SeedOverrides = Partial<Omit<MockConnectSession, 'agent_id' | 'vendor_key'>>;

function seedManualSession(overrides: SeedOverrides = {}): MockConnectSession {
	return seedMockAgentConnectSession({
		agent_id: 'agnt_1',
		vendor_key: 'acme-com',
		target_kind: 'api',
		resolved_flow: 'manual_api_key',
		reason: 'Need to list charges for the monthly report.',
		requested_permission_rules: [SUGGESTED_RULE],
		scheme: { type: 'api_key', location: 'header', field_name: 'X-Api-Key' },
		pinned_hosts: ['https://api.acme.example'],
		provenance: {
			origin: 'agent',
			catalog_api_id: null,
			submitted_by: 'agnt_1',
			source_url: 'https://acme.example/openapi.json',
			revision_id: 'rev_1',
		},
		agent: { agent_id: 'agnt_1', name: 'Scout', owner_id: 'usr_owner', status: 'active' },
		digest: 'dig_manual',
		...overrides,
	});
}

function seedAwaitingAppSession(overrides: SeedOverrides = {}): MockConnectSession {
	return seedMockAgentConnectSession({
		agent_id: 'agnt_1',
		vendor_key: 'github',
		state: 'awaiting_app',
		target_kind: 'api',
		resolved_flow: 'awaiting_app',
		requested_scopes: ['repo'],
		requested_permission_rules: [SUGGESTED_RULE],
		scheme: { type: 'oauth2', location: null, field_name: null },
		pinned_hosts: ['https://api.github.com'],
		provenance: {
			origin: 'catalog',
			catalog_api_id: 'github.com',
			submitted_by: null,
			source_url: null,
			revision_id: 'rev_gh',
		},
		agent: { agent_id: 'agnt_1', name: 'Scout', owner_id: 'usr_owner', status: 'active' },
		digest: 'dig_awaiting',
		...overrides,
	});
}

function candidate(overrides: Partial<ExistingCredential> = {}): ExistingCredential {
	return {
		credential_id: 'cred_gh_personal',
		name: 'Personal GitHub',
		type: 'oauth2_authorization_code',
		granted_scopes: ['repo'],
		missing_scopes: [],
		other_bound_agents: [],
		can_bind: true,
		needs_vendor_consent: false,
		...overrides,
	};
}

function renderApprove(sessionId: string, wrap?: (ui: ReactElement) => ReactElement) {
	const onBack = vi.fn();
	const onDone = vi.fn();
	const ui = (
		<VendorConnectFlow mode="approve" sessionId={sessionId} onBack={onBack} onDone={onDone} />
	);
	const view = renderWithProviders(wrap ? wrap(ui) : ui);
	return { ...view, onBack, onDone };
}

let sessionRequests: Request[] = [];
const record = ({ request }: { request: Request }): void => {
	if (new URL(request.url).pathname.startsWith('/connect-sessions/')) {
		sessionRequests.push(request);
	}
};

function mutatingRequests(): Request[] {
	return sessionRequests.filter((r) => r.method !== 'GET');
}

beforeEach(() => {
	resetConnectSessionsStore();
	resetCredentialsStore();
	worker.use(
		http.get('/agents', () =>
			HttpResponse.json({
				data: [{ id: 'agnt_1', name: 'Scout', actor_type: 'agent', status: 'active' }],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
	vi.spyOn(window, 'open').mockReturnValue(null);
	sessionRequests = [];
	worker.events.on('request:start', record);
});

afterEach(() => {
	worker.events.removeListener('request:start', record);
	vi.restoreAllMocks();
	resetConnectSessionsStore();
	resetCredentialsStore();
});

async function continueFromReview(user: ReturnType<typeof userEvent.setup>): Promise<void> {
	const continueButton = await screen.findByRole('button', { name: /^continue$/i });
	await pickDurationIfAsked(user);
	await user.click(continueButton);
	// The rules page, pre-filled with the agent's suggested rule.
	await screen.findByText(/permission rules/i);
}

describe('VendorConnectFlow approve — how long the access lasts', () => {
	const DAY = 86_400_000;

	it('preselects what the request asked for and sends it as the end date on confirm', async () => {
		const seeded = seedManualSession({ requested_expires_in: 'P7D' });
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const preset = (await screen.findByTestId('approve-duration-preset')) as HTMLSelectElement;
		expect(preset.value).toBe('7d');
		expect(screen.getByTestId('approve-access-summary')).toHaveTextContent(
			'The request asked for 7 days.',
		);
		// The approver shortens it to a day.
		await user.selectOptions(preset, '1d');
		await continueFromReview(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_live_123');
		const before = Date.now();
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		const body = getMockConnectSessions()[0].confirmBodies[0] as { expires_at?: string };
		const end = Date.parse(String(body.expires_at));
		expect(end).toBeGreaterThanOrEqual(before + DAY);
		expect(end).toBeLessThanOrEqual(Date.now() + DAY);
	});

	it("preselects a requested idle limit and sends the approver's choice", async () => {
		const seeded = seedManualSession({
			requested_expires_in: 'P30D',
			requested_max_idle_period: 'P14D',
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		expect(await screen.findByTestId('approve-access-summary')).toHaveTextContent(
			'The request asked for 30 days, ending if unused for 14 days.',
		);
		const idle = screen.getByTestId('approve-idle-preset') as HTMLSelectElement;
		expect(idle.value).toBe('14d');
		await user.selectOptions(idle, '7d');
		await continueFromReview(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_live_123');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		const body = getMockConnectSessions()[0].confirmBodies[0] as { max_idle_period?: string };
		expect(body.max_idle_period).toBe('P7D');
	});

	describe('an ask above the org maximum', () => {
		function orgLimits(
			limits: { expires?: string; idle?: string },
			gate?: Promise<void>,
		): void {
			worker.use(
				http.get('/agents/credentials\\:policy', async () => {
					if (gate) await gate;
					return HttpResponse.json({
						expires_in: { default: null, max: limits.expires ?? null },
						idle_period: { default: null, max: limits.idle ?? null },
						single_use_by_default: false,
					});
				}),
			);
		}

		it('is preselected as the maximum, and the note says so', async () => {
			orgLimits({ expires: 'P30D', idle: 'P14D' });
			const seeded = seedManualSession({
				requested_expires_in: 'P90D',
				requested_max_idle_period: 'P30D',
			});
			renderApprove(seeded.session_id);

			const preset = (await screen.findByTestId(
				'approve-duration-preset',
			)) as HTMLSelectElement;
			await waitFor(() => expect(preset.value).toBe('30d'));
			expect((screen.getByTestId('approve-idle-preset') as HTMLSelectElement).value).toBe(
				'14d',
			);
			const summary = screen.getByTestId('approve-access-summary');
			expect(summary).toHaveTextContent(
				'The request asked for 90 days, ending if unused for 30 days.',
			);
			expect(summary).toHaveTextContent(
				'This org allows a lifetime of at most 30 days and an idle limit of at most 14 days, so that is preselected.',
			);
		});

		it('comes down to the maximum when the limits arrive after the review', async () => {
			let release: () => void = () => {};
			orgLimits(
				{ expires: 'P30D' },
				new Promise<void>((resolve) => {
					release = resolve;
				}),
			);
			const seeded = seedManualSession({ requested_expires_in: 'P90D' });
			renderApprove(seeded.session_id);

			const preset = (await screen.findByTestId(
				'approve-duration-preset',
			)) as HTMLSelectElement;
			await waitFor(() => expect(preset.value).toBe('90d'));
			release();
			await waitFor(() => expect(preset.value).toBe('30d'));
		});

		it('never overrides a choice the approver already made', async () => {
			let release: () => void = () => {};
			orgLimits(
				{ expires: 'P30D' },
				new Promise<void>((resolve) => {
					release = resolve;
				}),
			);
			const seeded = seedManualSession({ requested_expires_in: 'P90D' });
			renderApprove(seeded.session_id);
			const user = userEvent.setup();

			const preset = (await screen.findByTestId(
				'approve-duration-preset',
			)) as HTMLSelectElement;
			await waitFor(() => expect(preset.value).toBe('90d'));
			await user.selectOptions(preset, '7d');
			release();

			await waitFor(() =>
				expect(screen.getByTestId('approve-access-summary')).toHaveTextContent(
					'This org allows a lifetime of at most 30 days',
				),
			);
			expect(preset.value).toBe('7d');
		});
	});

	it('waits for a choice when nothing was asked, and asks before No expiry', async () => {
		const seeded = seedManualSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const preset = (await screen.findByTestId('approve-duration-preset')) as HTMLSelectElement;
		expect(preset.value).toBe('');
		expect(screen.getByTestId('approve-access-summary')).not.toHaveTextContent(/asked for/);
		expect(screen.getByRole('button', { name: /^continue$/i })).toBeDisabled();

		await user.selectOptions(preset, 'custom');
		await user.selectOptions(screen.getByTestId('approve-duration-unit'), 'never');
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		expect(await screen.findByTestId('no-expiry-warning')).toHaveTextContent(
			'Scout will keep this access until someone removes it.',
		);
		await user.click(screen.getByRole('button', { name: 'Continue without expiry' }));
		expect(await screen.findByText(/permission rules/i)).toBeInTheDocument();
	});
});

describe('VendorConnectFlow approve — manual_* sessions', () => {
	it('shows provenance, agent and owner, scheme, pinned hosts and reason', async () => {
		const seeded = seedManualSession();
		const { container } = renderApprove(seeded.session_id);

		expect(await screen.findByText(/an agent is asking to connect/i)).toBeInTheDocument();
		expect(screen.getByText('Agent- or user-submitted spec')).toBeInTheDocument();
		expect(screen.getByText(/https:\/\/acme\.example\/openapi\.json/)).toBeInTheDocument();
		expect(screen.getByText('API key in the X-Api-Key header')).toBeInTheDocument();
		expect(
			within(screen.getByRole('list', { name: 'Pinned server hosts' })).getByText(
				'https://api.acme.example',
			),
		).toBeInTheDocument();
		expect(
			screen.getByText('Need to list charges for the monthly report.'),
		).toBeInTheDocument();
		expect(screen.getByText(/^Owner/)).toBeInTheDocument();
		// "Owner" names the role; no "User" type prefix in front of the name.
		expect(screen.getByText(/^Owner/)).not.toHaveTextContent(/^Owner\s*User\b/);
		// A manual session has no scopes to pick.
		expect(screen.queryByText('What can this agent do?')).not.toBeInTheDocument();
		await checkA11y(container);
	});

	it('marks a public-catalog API as such', async () => {
		const seeded = seedManualSession({
			provenance: {
				origin: 'catalog',
				catalog_api_id: 'acme.com',
				submitted_by: null,
				source_url: null,
				revision_id: 'rev_1',
			},
		});
		renderApprove(seeded.session_id);
		expect(await screen.findByText('Public catalog')).toBeInTheDocument();
		expect(screen.queryByText('Agent- or user-submitted spec')).not.toBeInTheDocument();
	});

	it('connects with the typed key, echoing the digest and agent, and never stores it', async () => {
		const seeded = seedManualSession();
		const { onDone } = renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));

		const field = await screen.findByLabelText(/^API key/);
		expect(field).toHaveAttribute('type', 'password');
		expect(field).toHaveAttribute('autocomplete', 'off');
		expect(screen.getByText('Sent as API key in the X-Api-Key header.')).toBeInTheDocument();
		await user.type(field, 'sk_live_secret_123');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		const [body] = getMockConnectSessions()[0].confirmBodies;
		expect(body).toEqual({
			kind: 'api_key',
			key: 'sk_live_secret_123',
			permission_rules: [SUGGESTED_RULE],
			expected_agent_id: 'agnt_1',
			digest: 'dig_manual',
			revision_id: expect.stringMatching(/^csr_/),
			// The end date chosen on the review, worked out on confirm.
			expires_at: expect.any(String),
			max_idle_period: null,
		});
		// Not persisted client-side anywhere the browser keeps across loads.
		const persisted =
			JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
		expect(persisted).not.toContain('sk_live_secret_123');
		// The input is gone with its step.
		expect(screen.queryByDisplayValue('sk_live_secret_123')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(onDone).toHaveBeenCalled();
	});

	it('closes a finished connect by itself after a short countdown', async () => {
		const seeded = seedManualSession();
		const { onDone } = renderApprove(seeded.session_id);
		const user = userEvent.setup();
		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_live_secret_123');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		expect(screen.getByTestId('auto-close-countdown')).toHaveTextContent(/Closing in \d…/);
		expect(screen.getByRole('status')).toHaveTextContent('closes by itself in 5 seconds');
		await waitFor(() => expect(onDone).toHaveBeenCalled(), { timeout: 8000 });
	});

	it('Stay open keeps a finished connect on screen', async () => {
		const seeded = seedManualSession();
		const { onDone } = renderApprove(seeded.session_id);
		const user = userEvent.setup();
		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_live_secret_123');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Stay open' }));
		expect(screen.queryByTestId('auto-close-countdown')).not.toBeInTheDocument();
		await new Promise((resolve) => setTimeout(resolve, 6500));
		expect(onDone).not.toHaveBeenCalled();
		expect(screen.getByText('Connected to acme-com')).toBeInTheDocument();
	});

	it('collects a username and password for a manual_basic session', async () => {
		const seeded = seedManualSession({
			resolved_flow: 'manual_basic',
			scheme: { type: 'basic', location: null, field_name: null },
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^Username/), 'svc-report');
		await user.type(screen.getByLabelText(/^Password/), 'hunter2');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			kind: 'basic',
			username: 'svc-report',
			password: 'hunter2',
		});
	});

	it('saves the approver’s rule changes as a revision and confirms that revision', async () => {
		const seeded = seedManualSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await user.click(screen.getByRole('button', { name: /add rule/i }));
		await user.type(screen.getByLabelText('Path pattern'), '/v1/refunds');
		await user.click(screen.getByRole('button', { name: /^add$/i }));
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));

		await user.type(await screen.findByLabelText(/^API key/), 'sk_1');
		const amends = mutatingRequests().filter((r) => r.url.endsWith(':amend'));
		expect(amends).toHaveLength(1);
		await user.click(screen.getByRole('button', { name: 'Connect' }));
		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();

		const [body] = getMockConnectSessions()[0].confirmBodies;
		expect(body).toMatchObject({
			digest: `dig_${seeded.session_id}_2`,
			revision_id: `csr_${seeded.session_id}_2`,
			permission_rules: [SUGGESTED_RULE, expect.objectContaining({ path: '/v1/refunds' })],
		});
	});

	it('confirms the agent’s own revision when the approver keeps its rules', async () => {
		const seeded = seedManualSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await screen.findByLabelText(/^API key/);
		expect(mutatingRequests().filter((r) => r.url.endsWith(':amend'))).toEqual([]);
	});

	it('says when this agent’s ask for the API was rejected before', async () => {
		const seeded = seedManualSession({ last_rejected_at: '2026-10-20T09:00:00Z' });
		renderApprove(seeded.session_id);
		expect(
			await screen.findByText(
				/This agent asked for this before, and the request was rejected/,
			),
		).toBeInTheDocument();
	});

	it('requires at least one rule and adds no fallback rule', async () => {
		const seeded = seedManualSession({ requested_permission_rules: [] });
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		expect(screen.getByText(/add at least one rule/i)).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /skip.*continue/i })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: /^continue$/i })).toBeDisabled();
	});

	it('sends the approver back to the rules on rules_required', async () => {
		const seeded = seedManualSession({
			confirmProblem: { status: 422, type: 'rules_required' },
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_1');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(
			await screen.findByText(/With no rules the agent can't call anything/i, {
				selector: '[role="alert"] *, [role="alert"]',
			}),
		).toBeInTheDocument();
		expect(screen.getByText(/permission rules/i)).toBeInTheDocument();
	});

	it('reloads the review on review_stale and confirms against the new digest', async () => {
		const seeded = seedManualSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_first');
		// What the approver reviewed changes under them.
		seeded.digest = 'dig_changed';
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(
			await screen.findByText(/This request changed while you were reviewing it/i),
		).toBeInTheDocument();
		expect(screen.getByText(/an agent is asking to connect/i)).toBeInTheDocument();
		// The secret was dropped with its step; the next confirm carries the new digest.
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await pickDurationIfAsked(user);
		await user.click(await screen.findByRole('button', { name: /^continue$/i }));
		const field = await screen.findByLabelText(/^API key/);
		expect(field).toHaveValue('');
		await user.type(field, 'sk_second');
		await user.click(screen.getByRole('button', { name: 'Connect' }));
		expect(await screen.findByText('Connected to acme-com')).toBeInTheDocument();
		const bodies = getMockConnectSessions()[0].confirmBodies;
		expect(bodies[bodies.length - 1]).toMatchObject({ kind: 'api_key', digest: 'dig_changed' });
	});

	it.each([
		['scheme_changed', /declared authentication changed/i],
		['servers_changed', /server hosts changed/i],
	])('ends the dialog with clear copy on %s', async (type, copy) => {
		const seeded = seedManualSession({
			confirmProblem: { status: 409, type, endsSession: true },
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^API key/), 'sk_1');
		await user.click(screen.getByRole('button', { name: 'Connect' }));

		expect(await screen.findByText(copy)).toBeInTheDocument();
		expect(screen.getByText("This request can't be approved")).toBeInTheDocument();
		expect(screen.queryByLabelText(/^API key/)).not.toBeInTheDocument();
	});

	it('blocks secret entry up front when the viewer cannot confirm', async () => {
		const seeded = seedManualSession({ can_confirm: false });
		renderApprove(seeded.session_id);

		expect(
			await screen.findByText(/Only the agent's owner .* or an org admin can approve/i),
		).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /^continue$/i })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
		expect(document.querySelector('input[type="password"]')).toBeNull();
	});

	it('explains an archived agent instead of offering approval', async () => {
		const seeded = seedManualSession({
			can_confirm: false,
			agent: { agent_id: 'agnt_1', name: 'Scout', owner_id: 'usr_owner', status: 'archived' },
		});
		renderApprove(seeded.session_id);
		expect(
			await screen.findByText("This agent is archived, so it can't be given a credential."),
		).toBeInTheDocument();
	});
});

describe('VendorConnectFlow approve — Not now vs Reject', () => {
	it('"Not now" closes with no server call and leaves the request open', async () => {
		const seeded = seedManualSession();
		const { onBack, unmount } = renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Not now' }));
		expect(onBack).toHaveBeenCalled();
		unmount();
		// Give any stray request a chance to fire.
		await new Promise((r) => setTimeout(r, 50));
		expect(mutatingRequests()).toEqual([]);
		expect(getMockConnectSessions()).toHaveLength(1);
	});

	it('unmounting before confirming sends nothing', async () => {
		const seeded = seedManualSession();
		const { unmount } = renderApprove(seeded.session_id);
		await screen.findByRole('button', { name: 'Not now' });
		unmount();
		await new Promise((r) => setTimeout(r, 50));
		expect(mutatingRequests()).toEqual([]);
	});

	it('"Reject" asks first, then calls :reject without a poll token', async () => {
		const seeded = seedManualSession();
		const { onDone } = renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Reject' }));
		const dialog = await screen.findByRole('dialog', { name: 'Reject this request?' });
		await user.click(within(dialog).getByRole('button', { name: 'Reject request' }));

		await waitFor(() => expect(onDone).toHaveBeenCalled());
		const reject = mutatingRequests().find((r) => new URL(r.url).pathname.endsWith(':reject'));
		expect(reject).toBeDefined();
		expect(new URL(reject!.url).searchParams.has('poll_token')).toBe(false);
		expect(mutatingRequests().some((r) => r.url.includes(':cancel'))).toBe(false);
		expect(getEndedMockConnectSessions()).toMatchObject([
			{ session_id: seeded.session_id, outcome: 'rejected' },
		]);
	});

	it('backing out of the reject confirmation sends nothing', async () => {
		const seeded = seedManualSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Reject' }));
		const dialog = await screen.findByRole('dialog', { name: 'Reject this request?' });
		await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'Reject this request?' }),
			).not.toBeInTheDocument(),
		);
		expect(mutatingRequests()).toEqual([]);
	});

	it('offers Not now and Reject on a vendor OAuth session too', async () => {
		const seeded = seedMockAgentConnectSession({ agent_id: 'agnt_1', vendor_key: 'github' });
		const { onBack } = renderApprove(seeded.session_id);
		const user = userEvent.setup();

		expect(await screen.findByRole('button', { name: 'Reject' })).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Not now' }));
		expect(onBack).toHaveBeenCalled();
		expect(mutatingRequests()).toEqual([]);
	});
});

describe('VendorConnectFlow approve — awaiting_app', () => {
	it('connects through the approver’s own OAuth client', async () => {
		const seeded = seedAwaitingAppSession();
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		expect(
			await screen.findByText(/there is no OAuth app to connect through yet/i),
		).toBeInTheDocument();
		expect(screen.getByRole('radio', { name: /Bring your own OAuth client/ })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		// No shared-app surface in this host: the option is not offered.
		expect(
			screen.queryByRole('radio', { name: /Register as a shared app/ }),
		).not.toBeInTheDocument();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^Client ID/), 'client-123');
		const secret = screen.getByLabelText(/^Client secret/);
		expect(secret).toHaveAttribute('autocomplete', 'off');
		await user.type(secret, 'shh-client-secret');
		// The API's declared endpoints are never a fallback: both are required.
		const submit = screen.getByRole('button', { name: 'Continue to sign-in' });
		expect(submit).toBeDisabled();
		await user.type(screen.getByLabelText(/^Authorize URL/), 'https://idp.example/authorize');
		await user.type(screen.getByLabelText(/^Token URL/), 'http://idp.example/token');
		expect(submit).toBeDisabled();
		await user.clear(screen.getByLabelText(/^Token URL/));
		await user.type(screen.getByLabelText(/^Token URL/), 'https://idp.example/token');
		await user.click(submit);

		expect(await screen.findByText(/Almost there/)).toBeInTheDocument();
		expect(window.open).toHaveBeenCalled();
		expect(getMockConnectSessions()[0].confirmBodies[0]).toEqual({
			kind: 'own_oauth_client',
			client_id: 'client-123',
			client_secret: 'shh-client-secret',
			authorize_url: 'https://idp.example/authorize',
			token_url: 'https://idp.example/token',
			confirmed_scopes: [],
			permission_rules: [SUGGESTED_RULE],
			expected_agent_id: 'agnt_1',
			digest: 'dig_awaiting',
			revision_id: expect.stringMatching(/^csr_/),
			// The end date chosen on the review, worked out on confirm.
			expires_at: expect.any(String),
			max_idle_period: null,
		});
	});

	it('offers a catalog spec’s endpoints filled in and folded away, editable on Change', async () => {
		const seeded = seedAwaitingAppSession({
			scheme: {
				type: 'oauth2',
				location: null,
				field_name: null,
				authorization_url: 'https://github.com/login/oauth/authorize',
				token_url: 'https://github.com/login/oauth/access_token',
			},
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^Client ID/), 'client-123');
		await user.type(screen.getByLabelText(/^Client secret/), 'shh-client-secret');
		// Folded away: no URL fields to fill, just where they point.
		expect(screen.queryByLabelText(/^Authorize URL/)).not.toBeInTheDocument();
		expect(screen.getByTestId('catalog-oauth-endpoints')).toHaveTextContent('github.com');
		const submit = screen.getByRole('button', { name: 'Continue to sign-in' });
		expect(submit).toBeEnabled();

		// Change opens them, filled in and editable.
		await user.click(screen.getByRole('button', { name: 'Change' }));
		expect(screen.getByLabelText(/^Authorize URL/)).toHaveValue(
			'https://github.com/login/oauth/authorize',
		);
		expect(screen.getByLabelText(/^Token URL/)).toHaveValue(
			'https://github.com/login/oauth/access_token',
		);
		await user.click(submit);

		expect(await screen.findByText(/Almost there/)).toBeInTheDocument();
		expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			kind: 'own_oauth_client',
			authorize_url: 'https://github.com/login/oauth/authorize',
			token_url: 'https://github.com/login/oauth/access_token',
		});
	});

	it('keeps the own-client form open on own_oauth_client_invalid', async () => {
		const seeded = seedAwaitingAppSession({
			confirmProblem: { status: 400, type: 'own_oauth_client_invalid' },
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.type(await screen.findByLabelText(/^Client ID/), 'client-123');
		await user.type(screen.getByLabelText(/^Client secret/), 'x');
		await user.type(screen.getByLabelText(/^Authorize URL/), 'https://idp.example/authorize');
		await user.type(screen.getByLabelText(/^Token URL/), 'https://idp.example/token');
		await user.click(screen.getByRole('button', { name: 'Continue to sign-in' }));

		expect(await screen.findByText(/Check the authorize and token URLs/)).toBeInTheDocument();
		expect(screen.getByLabelText(/^Client ID/)).toHaveValue('client-123');
	});

	it('binds an existing credential whose grant covers the request', async () => {
		const seeded = seedAwaitingAppSession({ existing_credentials: [candidate()] });
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const option = await screen.findByRole('radio', { name: /Use Personal GitHub/ });
		expect(option).toHaveAccessibleDescription(/covers the requested scopes \(repo\)/);
		await user.click(option);
		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));

		expect(await screen.findByText('Connected to github')).toBeInTheDocument();
		expect(getMockConnectSessions()).toHaveLength(0);
		const [ended] = getEndedMockConnectSessions();
		expect(ended.outcome).toBe('connected');
		expect(ended.confirmBodies[0]).toEqual({
			kind: 'existing_credential',
			credential_id: 'cred_gh_personal',
			permission_rules: [SUGGESTED_RULE],
			expected_agent_id: 'agnt_1',
			digest: 'dig_awaiting',
			revision_id: expect.stringMatching(/^csr_/),
			// The end date chosen on the review, worked out on confirm.
			expires_at: expect.any(String),
			max_idle_period: null,
		});
	});

	it('refuses a narrower credential it may not widen and explains why', async () => {
		const seeded = seedAwaitingAppSession({
			requested_scopes: ['repo', 'admin:org'],
			existing_credentials: [
				candidate({
					granted_scopes: ['repo'],
					missing_scopes: ['admin:org'],
					other_bound_agents: [{ agent_id: 'agnt_2', name: 'Helper' }],
					can_bind: false,
					needs_vendor_consent: true,
				}),
			],
		});
		renderApprove(seeded.session_id);

		const option = await screen.findByRole('radio', { name: /Use Personal GitHub/ });
		expect(option).toBeDisabled();
		expect(option).toHaveAccessibleDescription(/Missing admin:org/);
		expect(option).toHaveAccessibleDescription(/used by 1 other agent/);
		expect(option).toHaveAccessibleDescription(/only its owner or an admin can widen it/);
		// The way forward is a new credential: the session's own flow.
		expect(screen.getByRole('radio', { name: /Bring your own OAuth client/ })).toHaveAttribute(
			'aria-checked',
			'true',
		);
	});

	it('uses a narrower credential in one step, naming the other agents it reaches', async () => {
		const seeded = seedAwaitingAppSession({
			requested_scopes: ['repo', 'admin:org'],
			existing_credentials: [
				candidate({
					granted_scopes: ['repo'],
					missing_scopes: ['admin:org'],
					other_bound_agents: [{ agent_id: 'agnt_2', name: 'Helper' }],
					can_bind: true,
					needs_vendor_consent: true,
				}),
			],
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const option = await screen.findByRole('radio', {
			name: /Use Personal GitHub and add the missing scopes/,
		});
		expect(option).toHaveAccessibleDescription(/sign in again to add admin:org/);
		expect(option).toHaveAccessibleDescription(/also gives admin:org to 1 other agent/);
		await user.click(option);
		await continueFromReview(user);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));

		await waitFor(() =>
			expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
				kind: 'existing_credential',
				credential_id: 'cred_gh_personal',
			}),
		);
		expect(window.open).toHaveBeenCalledWith(
			'https://vendor.example.test/oauth/authorize?consent=cred_gh_personal',
			'_blank',
			'noopener,noreferrer',
		);
	});

	it('offers "Register as a shared app" only where the host provides that surface', async () => {
		const onRegisteredSpy = vi.fn();
		const surface: SharedAppSurface = {
			renderRegister: ({ displayName, onRegistered }) => (
				<button
					type="button"
					onClick={(): void => {
						onRegisteredSpy();
						onRegistered();
					}}
				>
					Register {displayName}
				</button>
			),
		};
		const seeded = seedAwaitingAppSession();
		renderApprove(seeded.session_id, (ui) => (
			<SharedAppSurfaceContext.Provider value={surface}>
				{ui}
			</SharedAppSurfaceContext.Provider>
		));
		const user = userEvent.setup();

		await user.click(await screen.findByRole('radio', { name: /Register as a shared app/ }));
		// The host renders its own entry; the dialog's Continue waits on it.
		expect(screen.getByRole('button', { name: /^continue$/i })).toBeDisabled();
		const reviewReads = sessionRequests.filter((r) => r.method === 'GET').length;
		await user.click(screen.getByRole('button', { name: 'Register github' }));
		expect(onRegisteredSpy).toHaveBeenCalled();
		// The review reloads so the session can resolve through the new app.
		await waitFor(() =>
			expect(sessionRequests.filter((r) => r.method === 'GET').length).toBeGreaterThan(
				reviewReads,
			),
		);
	});
});

describe('VendorConnectFlow approve — existing credential on a vendor session', () => {
	it('offers "Use an existing credential" alongside the sign-in', async () => {
		const seeded = seedMockAgentConnectSession({
			agent_id: 'agnt_1',
			vendor_key: 'github',
			existing_credentials: [candidate({ type: 'api_key', granted_scopes: null })],
		});
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		expect(await screen.findByRole('radio', { name: /Sign in to github/ })).toBeInTheDocument();
		const option = screen.getByRole('radio', { name: /Use Personal GitHub/ });
		expect(option).toHaveAccessibleDescription(/upstream permissions aren't visible/);
		await user.click(option);
		await pickDurationIfAsked(user);
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.click(await screen.findByRole('button', { name: /skip.*continue/i }));

		expect(await screen.findByText('Connected to github')).toBeInTheDocument();
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			kind: 'existing_credential',
			credential_id: 'cred_gh_personal',
			// The fallback rule is the approver's, so it is confirmed as their revision.
			digest: `dig_${seeded.session_id}_2`,
		});
	});
});

describe('VendorConnectFlow approve — a credential the agent already holds', () => {
	const bound = {
		expires_at: new Date(Date.now() + 9 * 86_400_000).toISOString(),
		max_idle_period: 'P14D',
		max_quota: 3,
		quota_remaining: 2,
		suspended: false,
		expired: false,
	};

	function seedHeld(agentBinding: typeof bound | null) {
		return seedMockAgentConnectSession({
			agent_id: 'agnt_1',
			vendor_key: 'github',
			existing_credentials: [
				candidate({ type: 'api_key', granted_scopes: null, agent_binding: agentBinding }),
			],
		});
	}

	it('says its limits stay as they are, disables the limit controls and sends none', async () => {
		const seeded = seedHeld(bound);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const preset = (await screen.findByTestId('approve-duration-preset')) as HTMLSelectElement;
		expect(preset).toBeEnabled();
		expect(screen.queryByTestId('approve-existing-binding')).toBeNull();

		await user.click(screen.getByRole('radio', { name: /Use Personal GitHub/ }));

		const note = await screen.findByTestId('approve-existing-binding');
		expect(note).toHaveTextContent('is already bound to this credential');
		expect(note).toHaveTextContent('its current limits stay as they are');
		expect(note).toHaveTextContent('expires in 9 days');
		expect(note).toHaveTextContent('2 of 3 uses left');
		expect(note).toHaveTextContent('ends if unused for 14 days');
		expect(preset).toBeDisabled();
		expect(
			screen.getByTestId('approve-idle-toggle').querySelector('[role=checkbox]'),
		).toBeDisabled();
		expect(
			screen.getByTestId('approve-single-use').querySelector('[role=checkbox]'),
		).toBeDisabled();
		expect(screen.getByTestId('approve-after-expiry')).toBeDisabled();

		// Nothing here is chosen, and Continue does not wait for it.
		expect(preset.value).toBe('');
		await user.click(screen.getByRole('button', { name: /^continue$/i }));
		await user.click(await screen.findByRole('button', { name: /skip.*continue/i }));

		expect(await screen.findByText('Connected to github')).toBeInTheDocument();
		const body = getEndedMockConnectSessions()[0].confirmBodies[0] as unknown as Record<
			string,
			unknown
		>;
		expect(body).toMatchObject({
			kind: 'existing_credential',
			credential_id: 'cred_gh_personal',
		});
		for (const field of [
			'expires_at',
			'max_idle_period',
			'max_quota',
			'idle_warning',
			'after_expiry',
		]) {
			expect(body).not.toHaveProperty(field);
		}
	});

	it('goes back to asking for limits when another way to connect is picked', async () => {
		const seeded = seedHeld(bound);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();
		await user.click(await screen.findByRole('radio', { name: /Use Personal GitHub/ }));
		expect(await screen.findByTestId('approve-existing-binding')).toBeVisible();

		await user.click(screen.getByRole('radio', { name: /Sign in to github/ }));

		expect(screen.queryByTestId('approve-existing-binding')).toBeNull();
		expect(screen.getByTestId('approve-duration-preset')).toBeEnabled();
	});

	it('keeps asking for limits for a credential the agent does not hold yet', async () => {
		const seeded = seedHeld(null);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('radio', { name: /Use Personal GitHub/ }));

		expect(screen.queryByTestId('approve-existing-binding')).toBeNull();
		expect(screen.getByTestId('approve-duration-preset')).toBeEnabled();
		expect(screen.getByRole('button', { name: /^continue$/i })).toBeDisabled();
	});
});

describe('open connect requests', () => {
	it('include sessions awaiting an OAuth app', async () => {
		const awaiting = seedAwaitingAppSession();
		const created = seedManualSession();
		const rows = await listOpenConnectRequests();
		expect(rows.map((r) => r.session_id).sort()).toEqual(
			[awaiting.session_id, created.session_id].sort(),
		);
	});
});
