import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { BindAgentDialog } from '@/modules/workspace/components/BindAgentDialog';
import { Toaster } from '@/shared/ui/Toaster';
import { clearAllToasts } from '@/shared/ui/toastStore';

/**
 * "Already bound" must come from EVERY page of the credential's agents: an
 * agent past the first page must not read as unbound (and be bound twice),
 * and nothing is bindable until the roster is whole.
 */

const CRED = makeMockCredential({
	credential_id: 'cred_big',
	name: 'Big key',
	api: { vendor: 'stripe', name: 'stripe-api', version: '' },
});

function pickerAgent(id: string) {
	return {
		id,
		name: id,
		status: 'active',
		created_at: '2026-01-01T00:00:00Z',
	};
}

function boundAgent(id: string) {
	return {
		agent_id: id,
		agent_name: id,
		bound_at: '2026-01-01T00:00:00Z',
		rule_set_id: null,
		status: 'active',
		suspended: false,
	};
}

let posted: string[] = [];

/** Two bound pages: `agent_1` on page 1, `agent_2` only on page 2. */
function serve(opts: { page2?: 'ok' | 'error' | 'hang' } = {}) {
	const page2 = opts.page2 ?? 'ok';
	worker.use(
		http.get('/agents', () =>
			HttpResponse.json({
				data: ['agent_1', 'agent_2', 'agent_3'].map(pickerAgent),
				has_more: false,
				next_cursor: null,
			}),
		),
		http.get('/credentials/:cid/agents', async ({ request }) => {
			const cursor = new URL(request.url).searchParams.get('cursor');
			if (!cursor) {
				return HttpResponse.json({
					data: [boundAgent('agent_1')],
					has_more: true,
					next_cursor: 'page-2',
				});
			}
			if (page2 === 'error') {
				return HttpResponse.json({ detail: 'boom' }, { status: 500 });
			}
			if (page2 === 'hang') await new Promise(() => {});
			return HttpResponse.json({
				data: [boundAgent('agent_2')],
				has_more: false,
				next_cursor: null,
			});
		}),
		http.post('/agents/:id/credentials', ({ params }) => {
			posted.push(String(params.id));
			return HttpResponse.json({}, { status: 201 });
		}),
	);
}

function renderDialog() {
	return renderWithProviders(
		<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />,
	);
}

function optionFor(name: string): HTMLElement {
	const row = screen
		.getAllByTestId('bind-agent-option')
		.find((li) => within(li).queryByText(name) != null);
	if (!row) throw new Error(`no option for ${name}`);
	return row;
}

describe('BindAgentDialog — already-bound agents across pages', () => {
	beforeEach(() => {
		setToken('test-token');
		posted = [];
	});

	it('marks an agent bound only on page 2 as already bound', async () => {
		serve();
		renderDialog();
		await waitFor(() =>
			expect(within(optionFor('agent_2')).getByText('Already bound')).toBeInTheDocument(),
		);
		expect(within(optionFor('agent_1')).getByText('Already bound')).toBeInTheDocument();
		expect(within(optionFor('agent_3')).queryByText('Already bound')).not.toBeInTheDocument();
		expect(within(optionFor('agent_2')).getByRole('checkbox')).toBeDisabled();

		const user = userEvent.setup();
		await user.click(within(optionFor('agent_3')).getByRole('checkbox'));
		await user.click(screen.getByRole('radio', { name: /Allow all/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(posted).toEqual(['agent_3']));
	});

	it('holds every tick and the confirm while the roster is still draining', async () => {
		serve({ page2: 'hang' });
		renderDialog();
		expect(await screen.findByTestId('bind-agent-checking')).toBeInTheDocument();
		expect(within(optionFor('agent_2')).getByRole('checkbox')).toBeDisabled();
		expect(within(optionFor('agent_3')).getByRole('checkbox')).toBeDisabled();
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});

	it('says so (with a retry) when a later page fails, and stays unbindable', async () => {
		serve({ page2: 'error' });
		renderDialog();
		expect(
			await screen.findByText('Couldn’t check which agents already use this credential.'),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
		expect(within(optionFor('agent_3')).getByRole('checkbox')).toBeDisabled();
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});
});

describe('BindAgentDialog — what the agent may call', () => {
	let ruleCalls: { agent: string; body: unknown }[] = [];
	beforeEach(() => {
		setToken('test-token');
		posted = [];
		ruleCalls = [];
	});

	/** No one bound yet; PUT rules answers `failFor` agents with a 500. */
	function serveRules({ failFor = [] as string[] } = {}) {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: [
						pickerAgent('agent_1'),
						pickerAgent('agent_2'),
						{ ...pickerAgent('agent_pending'), status: 'pending' },
						{ ...pickerAgent('agent_disabled'), status: 'disabled' },
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials/:cid/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.post('/agents/:id/credentials', ({ params }) => {
				posted.push(String(params.id));
				return HttpResponse.json({}, { status: 201 });
			}),
			http.put('/credentials/:cid/agents/:aid/permissions', async ({ params, request }) => {
				const agent = String(params.aid);
				ruleCalls.push({ agent, body: await request.json() });
				if (failFor.includes(agent)) {
					return HttpResponse.json({ detail: 'boom' }, { status: 500 });
				}
				return HttpResponse.json({ data: [] });
			}),
		);
	}

	it('offers only active agents', async () => {
		serveRules();
		renderDialog();
		await screen.findByRole('checkbox', { name: 'agent_1' });
		expect(screen.queryByText('agent_pending')).not.toBeInTheDocument();
		expect(screen.queryByText('agent_disabled')).not.toBeInTheDocument();
	});

	it('preselects no rules; Bind waits for a preset and applies it to every agent', async () => {
		serveRules();
		const user = userEvent.setup();
		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('checkbox', { name: 'agent_2' }));
		for (const radio of screen.getAllByRole('radio')) {
			expect(radio).toHaveAttribute('aria-checked', 'false');
		}
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();

		await user.click(screen.getByRole('radio', { name: /Read-only/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(ruleCalls).toHaveLength(2));
		expect(posted).toEqual(['agent_1', 'agent_2']);
		for (const call of ruleCalls)
			expect(call.body).toEqual([{ effect: 'allow', methods: ['GET'] }]);
	});

	it('the shared access step: same question, same presets, Allow all writes `.*`, no inline tester', async () => {
		serveRules();
		const user = userEvent.setup();
		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		const group = screen.getByRole('radiogroup', { name: 'What can this agent call?' });
		expect(
			within(group)
				.getAllByRole('radio')
				.map((r) => r.textContent),
		).toEqual([
			expect.stringMatching(/^Allow all operations/),
			expect.stringMatching(/^Read-only \(GET only\)/),
			expect.stringMatching(/^Custom rules/),
		]);
		expect(screen.getByTestId('bind-agent-rules')).toContainElement(group);
		// The dry run is the Add-APIs queue's; the workspace bind stays as it was.
		expect(screen.queryByText('Try a request')).not.toBeInTheDocument();
		await user.click(within(group).getByRole('radio', { name: /Custom rules/ }));
		expect(screen.queryByLabelText('Request path')).not.toBeInTheDocument();

		await user.click(within(group).getByRole('radio', { name: /Allow all/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0].body).toEqual([{ effect: 'allow', path: '.*' }]);
	});

	it('Custom rules needs at least one rule before Bind is enabled', async () => {
		serveRules();
		const user = userEvent.setup();
		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();

		await user.click(screen.getByRole('button', { name: 'Add rule' }));
		await user.click(screen.getByRole('button', { name: 'DELETE' }));
		await user.click(screen.getByRole('button', { name: 'Add' }));
		expect(screen.getByTestId('bind-agent-confirm')).toBeEnabled();
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0].body).toEqual([{ effect: 'allow', methods: ['DELETE'] }]);
	});

	it('keeps a bind whose rules failed, says it is blocked, and Retry saves only those rules', async () => {
		serveRules({ failFor: ['agent_2'] });
		const user = userEvent.setup();
		let closed = false;
		renderWithProviders(
			<BindAgentDialog
				open
				onClose={() => {
					closed = true;
				}}
				credentials={[CRED]}
				apiLabel="Stripe"
			/>,
		);
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('checkbox', { name: 'agent_2' }));
		await user.click(screen.getByRole('radio', { name: /Allow all/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));

		const failed = await screen.findByTestId('bind-agent-rules-failed');
		expect(failed).toHaveTextContent("Bound, but the access rules weren't saved");
		expect(failed).toHaveTextContent('agent_2 is bound but blocked');
		expect(posted).toEqual(['agent_1', 'agent_2']);
		expect(closed).toBe(false);

		// The retry re-saves for agent_2 only — no second bind.
		worker.use(
			http.put('/credentials/:cid/agents/:aid/permissions', async ({ params, request }) => {
				ruleCalls.push({ agent: String(params.aid), body: await request.json() });
				return HttpResponse.json({ data: [] });
			}),
		);
		ruleCalls = [];
		await user.click(within(failed).getByRole('button', { name: /try again/i }));
		await waitFor(() => expect(closed).toBe(true));
		expect(ruleCalls.map((c) => c.agent)).toEqual(['agent_2']);
		expect(posted).toEqual(['agent_1', 'agent_2']);
	});

	it('says every agent is bound instead of listing disabled rows', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: [pickerAgent('agent_1')],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials/:cid/agents', () =>
				HttpResponse.json({
					data: [boundAgent('agent_1')],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderDialog();
		expect(await screen.findByTestId('bind-agent-all-bound')).toBeInTheDocument();
		expect(screen.queryByTestId('bind-agent-list')).not.toBeInTheDocument();
	});

	it('clears a rules-save failure banner when the dialog is reopened', async () => {
		serveRules({ failFor: ['agent_1'] });
		const user = userEvent.setup();
		const ui = (open: boolean) => (
			<BindAgentDialog
				open={open}
				onClose={() => {}}
				credentials={[CRED]}
				apiLabel="Stripe"
			/>
		);
		const { rerender } = renderWithProviders(ui(true));
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Allow all/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		expect(await screen.findByTestId('bind-agent-rules-failed')).toBeInTheDocument();
		rerender(ui(false));
		rerender(ui(true));
		await screen.findByTestId('bind-agent-list');
		expect(screen.queryByTestId('bind-agent-rules-failed')).not.toBeInTheDocument();
	});
});

describe('BindAgentDialog — what Allow all reaches, and naming the credential', () => {
	beforeEach(() => {
		setToken('test-token');
		posted = [];
		clearAllToasts();
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: [pickerAgent('agent_1')],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials/:cid/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.post('/agents/:id/credentials', ({ params }) => {
				posted.push(String(params.id));
				return HttpResponse.json({}, { status: 201 });
			}),
			http.put('/credentials/:cid/agents/:aid/permissions', () =>
				HttpResponse.json({ data: [] }),
			),
		);
	});

	const allowAll = () => screen.findByRole('radio', { name: /Allow all/ });

	it('a pinned credential: every operation of this API, no coverage note', async () => {
		const pinned = makeMockCredential({
			credential_id: 'cred_pin',
			name: 'Pinned',
			api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
		});
		renderWithProviders(
			<BindAgentDialog open onClose={() => {}} credentials={[pinned]} apiLabel="Stripe" />,
		);
		expect(await allowAll()).toHaveAccessibleDescription(
			expect.stringContaining('Every operation of this API'),
		);
		expect(screen.queryByTestId('bind-agent-coverage-note')).not.toBeInTheDocument();
	});

	it('an any-version credential: reaches every version, including later ones', async () => {
		renderWithProviders(
			<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />,
		);
		const radio = await allowAll();
		expect(radio).toHaveAccessibleDescription(
			expect.stringContaining('including versions added later'),
		);
		expect(screen.getByTestId('bind-agent-coverage-note')).toHaveTextContent(
			'covers every version of this API',
		);
	});

	it('a vendor-wide credential: reaches every API and version of the vendor', async () => {
		const wide = makeMockCredential({
			credential_id: 'cred_wide',
			name: 'Vendor key',
			api: { vendor: 'stripe', name: '', version: '' },
		});
		renderWithProviders(
			<BindAgentDialog open onClose={() => {}} credentials={[wide]} apiLabel="Stripe" />,
		);
		expect(await allowAll()).toHaveAccessibleDescription(
			expect.stringContaining('every API and version this credential covers'),
		);
		expect(screen.getByTestId('bind-agent-coverage-note')).toHaveTextContent(
			'every API of its vendor',
		);
	});

	it('names a same-named credential in the toast the way the picker does', async () => {
		const a = makeMockCredential({
			credential_id: 'cred_aaaa1111',
			name: 'Shared key',
			api: { vendor: 'stripe', name: 'stripe-api', version: '' },
		});
		const b = makeMockCredential({
			credential_id: 'cred_bbbb4b2b',
			name: 'Shared key',
			api: { vendor: 'stripe', name: 'stripe-api', version: '' },
		});
		const user = userEvent.setup();
		renderWithProviders(
			<>
				<BindAgentDialog
					open
					onClose={() => {}}
					credentials={[a, b]}
					apiLabel="Stripe"
					initialCredentialId="cred_bbbb4b2b"
				/>
				<Toaster />
			</>,
		);
		const select = await screen.findByTestId('bind-agent-credential');
		const picked = within(select).getByRole('option', { selected: true }).textContent!;
		expect(picked).toMatch(/^Shared key · /);
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Read-only/ }));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		expect(
			await screen.findByText(`Bound “${picked}” to agent_1, with access rules.`),
		).toBeInTheDocument();
	});
});
