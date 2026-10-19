import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
import {
	getEndedMockConnectSessions,
	getMockConnectSessions,
	resetConnectSessionsStore,
	resetCredentialsStore,
	seedMockAgentConnectSession,
} from '@/shared/credentials/mocks/handlers';
import type { EditRuleView, ReviewEdit } from '@/shared/credentials/api/vendors-types';

/**
 * Approve mode for an edit session: the combined diff of the binding's access
 * today against the request, the "this request changed" pause, what the
 * confirm sends, and the vendor consent for added scopes.
 */

const READ: EditRuleView = {
	id: 'apr_read',
	effect: 'allow',
	methods: ['GET'],
	path: '/v1/',
	match_mode: 'prefix',
};
const DENY: EditRuleView = {
	id: 'apr_deny',
	effect: 'deny',
	methods: ['DELETE'],
	path: '/v1/',
	match_mode: 'prefix',
};
const SEND: EditRuleView = {
	id: null,
	effect: 'allow',
	methods: ['POST'],
	path: '/v1/send',
	match_mode: 'exact',
};

function editBlock(overrides: Partial<ReviewEdit> = {}): ReviewEdit {
	return {
		binding: { agent_id: 'agnt_1', credential_id: 'cred_gmail', credential_name: 'Work Gmail' },
		revision: { id: 'csr_1', number: 1 },
		current: {
			rules: [READ, DENY],
			rule_set: null,
			suspended: false,
			granted_scopes: ['gmail.readonly'],
		},
		result: {
			target: 'binding',
			rules: [READ, SEND],
			scopes: ['gmail.readonly'],
			suspended: false,
		},
		widens_access: true,
		other_bound_agents: [],
		needs_vendor_consent: false,
		...overrides,
	};
}

function seedEdit(edit: ReviewEdit) {
	return seedMockAgentConnectSession({
		agent_id: 'agnt_1',
		vendor_key: 'gmail',
		resolved_flow: 'edit',
		reason: 'Needs to send replies',
		agent: { agent_id: 'agnt_1', name: 'Scout', owner_id: 'usr_owner', status: 'active' },
		edit,
	});
}

function renderApprove(sessionId: string) {
	const onBack = vi.fn();
	const onDone = vi.fn();
	renderWithProviders(
		<VendorConnectFlow mode="approve" sessionId={sessionId} onBack={onBack} onDone={onDone} />,
	);
	return { onBack, onDone };
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
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe('VendorConnectFlow approve — edit sessions', () => {
	it('shows the combined diff and approves the revision it shows', async () => {
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const rules = await screen.findByRole('region', { name: 'Rule changes' });
		const rows = within(rules).getAllByRole('listitem');
		expect(rows.map((r) => r.textContent)).toEqual([
			expect.stringContaining('GET /v1/ (prefix)'),
			expect.stringMatching(/POST \/v1\/send \(exact\).*Added.*Widens/),
			expect.stringMatching(/DELETE \/v1\/ \(prefix\).*Removed.*Widens/),
		]);
		expect(screen.getByText(/more access than it has today/)).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('Access to Work Gmail updated')).toBeInTheDocument();
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toEqual({
			kind: 'edit',
			revision_id: 'csr_1',
			permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
				{ effect: 'allow', methods: ['POST'], path: '/v1/send', match_mode: 'exact' },
			],
			resume: false,
			confirmed_scopes: null,
		});
	});

	it('refreshes the binding, the agents the scopes reach and the rule sets once applied', async () => {
		const seeded = seedEdit(
			editBlock({ other_bound_agents: [{ agent_id: 'agnt_2', name: 'Helper' }] }),
		);
		const { queryClient } = renderWithProviders(
			<VendorConnectFlow
				mode="approve"
				sessionId={seeded.session_id}
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Approve' }));
		await screen.findByText('Access to Work Gmail updated');
		const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
		expect(keys).toEqual(
			expect.arrayContaining([
				['agents', 'binding-permissions', 'agnt_1', 'cred_gmail'],
				['agents', 'binding-permissions', 'agnt_2', 'cred_gmail'],
				['agents', 'credential-bindings', 'agnt_1'],
				['agents', 'rule-set'],
			]),
		);
	});

	it('pauses Approve when the request changes under the reviewer', async () => {
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();
		await screen.findByRole('region', { name: 'Rule changes' });

		// The agent amends: a new revision lands on the next review read.
		const [session] = getMockConnectSessions();
		session.edit = editBlock({
			revision: { id: 'csr_2', number: 2 },
			result: {
				target: 'binding',
				rules: [READ],
				scopes: ['gmail.readonly'],
				suspended: false,
			},
		});
		await waitFor(
			() =>
				expect(
					screen.getByText(/This request changed while you were reviewing it/),
				).toBeInTheDocument(),
			{ timeout: 15_000 },
		);
		expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();

		await user.click(screen.getByRole('button', { name: 'Show the new version' }));
		expect(screen.getByText('Revision 2')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Approve' }));
		await screen.findByText('Access to Work Gmail updated');
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			revision_id: 'csr_2',
		});
	}, 30_000);

	it('runs the vendor consent for added scopes and names the other agents they reach', async () => {
		const seeded = seedEdit(
			editBlock({
				result: {
					target: 'binding',
					rules: [READ],
					scopes: ['gmail.readonly', 'gmail.send'],
					suspended: false,
				},
				other_bound_agents: [{ agent_id: 'agnt_2', name: 'Helper' }],
				needs_vendor_consent: true,
			}),
		);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const scopes = await screen.findByRole('region', { name: 'Scope changes' });
		expect(scopes).toHaveTextContent('gmail.send');
		expect(scopes).toHaveTextContent(/reach 1 other agent/);

		await user.click(screen.getByRole('button', { name: 'Approve and sign in' }));
		await waitFor(() =>
			expect(window.open).toHaveBeenCalledWith(
				`https://vendor.example.test/oauth/authorize?edit=${seeded.session_id}`,
				'_blank',
				'noopener,noreferrer',
			),
		);
		expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			kind: 'edit',
			confirmed_scopes: ['gmail.readonly', 'gmail.send'],
		});
	});

	it("shows each revision's own change in the history", async () => {
		const seeded = seedEdit(editBlock({ revision: { id: 'csr_2', number: 2 } }));
		seeded.revisions = [
			{
				id: 'csr_1',
				number: 1,
				created_by: 'agnt_1',
				created_at: '2026-10-19T10:00:00Z',
				proposal: {},
				result: { rules: [READ, DENY], scopes: ['gmail.readonly'] },
			},
			{
				id: 'csr_2',
				number: 2,
				created_by: 'agnt_1',
				created_at: '2026-10-19T10:05:00Z',
				proposal: {},
				result: { rules: [READ, SEND], scopes: ['gmail.readonly'] },
			},
		];
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'History' }));
		const history = await screen.findByRole('list', { name: 'Request history' });
		const entries = within(history).getAllByRole('listitem', { name: undefined });
		const revisionTwo = entries.find((e) => e.textContent?.startsWith('Revision 2'));
		expect(revisionTwo).toBeDefined();
		expect(revisionTwo).toHaveTextContent(/POST \/v1\/send.*Added/);
		expect(revisionTwo).toHaveTextContent(/DELETE \/v1\/.*Removed/);
		expect(within(history).getByText('Revision 1').closest('li')).toHaveTextContent(
			'No rule changes.',
		);
	});

	it('offers to resume a suspended binding', async () => {
		const seeded = seedEdit(
			editBlock({
				current: { rules: [READ], rule_set: null, suspended: true, granted_scopes: null },
				result: { target: 'binding', rules: [READ], scopes: [], suspended: false },
			}),
		);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const resume = await screen.findByRole('checkbox', { name: /Switch this binding back on/ });
		expect(resume).toBeChecked();
		await user.click(screen.getByRole('button', { name: 'Approve' }));
		await screen.findByText('Access to Work Gmail updated');
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toMatchObject({ resume: true });
	});

	it('heads the approval sheet as an access change, not a connect', async () => {
		const seeded = seedEdit(editBlock());
		renderWithProviders(
			<CreateCredentialFlow
				open
				onClose={vi.fn()}
				onCreated={vi.fn()}
				approvalSession={{ sessionId: seeded.session_id }}
			/>,
		);
		expect(
			await screen.findByRole('heading', { name: 'Review access change' }),
		).toBeInTheDocument();
		expect(screen.getByText('An agent is asking to change its access.')).toBeInTheDocument();
		expect(screen.queryByText('Approve integration')).not.toBeInTheDocument();
	});
});
