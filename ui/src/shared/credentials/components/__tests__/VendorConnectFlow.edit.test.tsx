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
import { seedCredentialBindings } from '@/modules/agents/mocks/handlers';
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
			resolve_conflicts: false,
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

	it('lists scopes to remove and runs the sign-in for the narrower set', async () => {
		const seeded = seedEdit(
			editBlock({
				current: {
					rules: [READ, DENY],
					rule_set: null,
					suspended: false,
					granted_scopes: ['gmail.readonly', 'gmail.modify'],
				},
				result: {
					target: 'binding',
					rules: [READ, DENY],
					scopes: ['gmail.readonly'],
					suspended: false,
				},
				widens_access: false,
				needs_vendor_consent: true,
			}),
		);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const scopes = await screen.findByRole('region', { name: 'Scope changes' });
		const rows = within(scopes).getAllByRole('listitem');
		expect(rows.map((r) => r.textContent)).toEqual([
			expect.stringMatching(/gmail\.modify.*Removed/),
			expect.stringMatching(/gmail\.readonly.*Granted/),
		]);
		await user.click(screen.getByRole('button', { name: 'Approve and sign in' }));
		await waitFor(() =>
			expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
				confirmed_scopes: ['gmail.readonly'],
			}),
		);

		// The vendor kept the removed scope: the rest applied, and it says so.
		const [session] = getMockConnectSessions();
		session.status = { ...session.status, status: 'applied', error_code: 'scopes_not_removed' };
		expect(
			await screen.findByText(/the vendor kept scopes it removes/, undefined, {
				timeout: 10_000,
			}),
		).toBeInTheDocument();
	}, 20_000);

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

		// Who changed it and what they did, with the way to it and to the history.
		expect(screen.getByRole('alert')).toHaveTextContent(/Revision 2: .*removed 1 rule, .* ago/);
		expect(screen.getByRole('button', { name: 'See history' })).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Review the new version' }));
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
				proposal: { reason: 'Needs to read mail' },
				result: { rules: [READ, DENY], scopes: ['gmail.readonly'] },
			},
			{
				id: 'csr_2',
				number: 2,
				created_by: 'usr_owner',
				created_at: '2026-10-19T10:05:00Z',
				proposal: { rules: { set: [READ, SEND] }, reason: 'Send only, no deletes' },
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
		// What each revision did, why, and whether the agent or a reviewer made it.
		expect(revisionTwo).toHaveTextContent('Reviewer');
		expect(revisionTwo).toHaveTextContent('Replaced the rule list.');
		expect(revisionTwo).toHaveTextContent('“Send only, no deletes”');
		const revisionOne = within(history).getByText('Revision 1').closest('li');
		expect(revisionOne).toHaveTextContent('No rule changes.');
		expect(revisionOne).toHaveTextContent('Agent');
		expect(revisionOne).toHaveTextContent('“Needs to read mail”');
		// The front page names the revision that added each rule.
		const front = screen.getByRole('region', { name: 'Rule changes' });
		expect(
			within(front)
				.getByText(/POST \/v1\/send/)
				.closest('li'),
		).toHaveTextContent('Added in revision 2');
	});

	it('saves nothing when the reviewer continues without changing the rules', async () => {
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();
		const amends: Request[] = [];
		const record = ({ request }: { request: Request }): void => {
			if (request.url.endsWith(':amend')) amends.push(request);
		};
		worker.events.on('request:start', record);
		try {
			await user.click(await screen.findByRole('button', { name: 'Edit rules' }));
			await user.click(await screen.findByRole('button', { name: 'Continue' }));
			await screen.findByRole('region', { name: 'Rule changes' });
			expect(screen.getByText('Revision 1')).toBeInTheDocument();
			expect(amends).toEqual([]);
		} finally {
			worker.events.removeListener('request:start', record);
		}
	});

	it('says Approve was refused when the request changed first', async () => {
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();
		await screen.findByRole('region', { name: 'Rule changes' });
		// Amended between the last review read and the click.
		seeded.edit = editBlock({ revision: { id: 'csr_2', number: 2 } });
		await user.click(screen.getByRole('button', { name: 'Approve' }));
		expect(
			await screen.findByText(
				'Not approved: this request changed while you were reviewing it.',
			),
		).toBeInTheDocument();
		await user.click(await screen.findByRole('button', { name: 'Review the new version' }));
		expect(screen.getByText('Revision 2')).toBeInTheDocument();
	});

	it('says when this agent’s ask was rejected before', async () => {
		const seeded = seedEdit(editBlock());
		seeded.last_rejected_at = '2026-10-20T09:00:00Z';
		renderApprove(seeded.session_id);
		expect(
			await screen.findByText(
				/This agent asked for this before, and the request was rejected/,
			),
		).toBeInTheDocument();
	});

	it('lists the conflicts and approves the reviewer’s rules to resolve them', async () => {
		const seeded = seedEdit(
			editBlock({ conflicts: [{ kind: 'missing_rule', rule_id: 'apr_deny' }] }),
		);
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		const conflicts = await screen.findByRole('region', { name: 'Conflicts' });
		expect(conflicts).toHaveTextContent(
			'A rule it names (apr_deny) was removed since it asked.',
		);
		await user.click(screen.getByRole('button', { name: 'Resolve and approve' }));
		await screen.findByText('Access to Work Gmail updated');
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			resolve_conflicts: true,
		});
	});

	it('shows who asked for each change, and why', async () => {
		const seeded = seedEdit(
			editBlock({
				result: {
					target: 'binding',
					rules: [
						READ,
						{ ...SEND, comment: 'To send drafted replies', added_by: 'agnt_1' },
					],
					scopes: ['gmail.readonly', 'gmail.send'],
					scope_notes: {
						'gmail.send': { added_by: 'agnt_1', comment: 'Needed to send' },
					},
					suspended: false,
				},
				needs_vendor_consent: true,
			}),
		);
		renderApprove(seeded.session_id);

		const rules = await screen.findByRole('region', { name: 'Rule changes' });
		const added = within(rules)
			.getAllByRole('listitem')
			.find((r) => r.textContent?.includes('/v1/send'));
		expect(added).toHaveTextContent(/Requested by agent.*To send drafted replies/);
		const scopes = screen.getByRole('region', { name: 'Scope changes' });
		const send = within(scopes)
			.getAllByRole('listitem')
			.find((r) => r.textContent?.startsWith('gmail.send'));
		expect(send).toHaveTextContent(/Added.*Requested by agent.*Needed to send/);
	});

	it('saves the reviewer’s scope change as a revision and approves it', async () => {
		seedCredentialBindings([
			{
				agent_id: 'agnt_1',
				credential_id: 'cred_gmail',
				name: 'Work Gmail',
				granted_scopes: ['gmail.readonly'],
				scope_catalog: [
					{ name: 'gmail.readonly', classification: 'read', description: 'Read mail.' },
					{ name: 'gmail.labels', classification: 'write', description: 'Labels.' },
				],
			},
		]);
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Edit scopes' }));
		await user.click(await screen.findByText('gmail.labels', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Save scopes' }));

		// The front page shows the reviewer's revision.
		const scopes = await screen.findByRole('region', { name: 'Scope changes' });
		await waitFor(() => expect(scopes).toHaveTextContent(/gmail\.labels.*Added by/), {
			timeout: 10_000,
		});
		expect(screen.getByText('Revision 2')).toBeInTheDocument();
		expect(screen.queryByText(/This request changed while you were reviewing it/)).toBeNull();

		await user.click(screen.getByRole('button', { name: 'Approve and sign in' }));
		await waitFor(() =>
			expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
				revision_id: `csr_${seeded.session_id}_2`,
				confirmed_scopes: ['gmail.labels', 'gmail.readonly'],
			}),
		);
	}, 20_000);

	it('saves the reviewer’s rule change as a revision shown on the front page', async () => {
		const seeded = seedEdit(editBlock());
		renderApprove(seeded.session_id);
		const user = userEvent.setup();

		await user.click(await screen.findByRole('button', { name: 'Edit rules' }));
		// Drop the agent's send rule, the last one.
		const remove = await screen.findAllByRole('button', { name: 'Delete rule' });
		await user.click(remove[remove.length - 1]);
		await user.click(screen.getByRole('button', { name: 'Continue' }));

		const rules = await screen.findByRole('region', { name: 'Rule changes' });
		await waitFor(() => expect(rules).not.toHaveTextContent('/v1/send'), { timeout: 10_000 });
		expect(screen.getByText('Revision 2')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Approve' }));
		await screen.findByText('Access to Work Gmail updated');
		expect(getEndedMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
			revision_id: `csr_${seeded.session_id}_2`,
			permission_rules: [
				{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
			],
		});
	}, 20_000);

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
