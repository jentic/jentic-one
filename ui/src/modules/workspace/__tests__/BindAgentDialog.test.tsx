import { describe, it, expect, beforeEach, vi } from 'vitest';
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

/** Resolves once `el` has stopped moving (the reveal / dialog animations done). */
async function settled(el: HTMLElement): Promise<void> {
	const frame = (): Promise<number> =>
		new Promise((r) => requestAnimationFrame(() => r(el.getBoundingClientRect().top)));
	let last = await frame();
	for (let stable = 0; stable < 5;) {
		const now = await frame();
		stable = now === last ? stable + 1 : 0;
		last = now;
	}
}

function renderDialog() {
	return renderWithProviders(
		<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />,
	);
}

/** Choose how long the access lasts (unless the test already has), then Bind. */
async function bind(user: ReturnType<typeof userEvent.setup>, duration = '7d'): Promise<void> {
	const preset = screen.getByTestId('bind-agent-duration-preset') as HTMLSelectElement;
	if (preset.value === '') await user.selectOptions(preset, duration);
	await user.click(screen.getByTestId('bind-agent-confirm'));
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
		await user.click(screen.getByRole('radio', { name: /Full access/ }));
		await bind(user);
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

		await user.click(screen.getByRole('radio', { name: /^Read only$/ }));
		await bind(user);
		await waitFor(() => expect(ruleCalls).toHaveLength(2));
		expect(posted).toEqual(['agent_1', 'agent_2']);
		for (const call of ruleCalls)
			expect(call.body).toEqual([{ effect: 'allow', methods: ['GET'] }]);
	});

	it('"Read only, ask before anything else" writes the GET allow, then a catch-all Ask', async () => {
		serveRules();
		const user = userEvent.setup();
		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /ask before anything else/ }));
		await bind(user);
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0].body).toEqual([
			{ effect: 'allow', methods: ['GET'] },
			{ effect: 'require-approval', path: '.*' },
		]);
	});

	it('the shared access step: same question, same presets, Full access writes `.*`, no inline tester', async () => {
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
			expect.stringMatching(/^Read onlyGET/),
			expect.stringMatching(/^Read only, ask before anything else/),
			expect.stringMatching(/^Full access/),
			expect.stringMatching(/^Custom rules/),
		]);
		expect(screen.getByTestId('bind-agent-rules')).toContainElement(group);
		// The dry run is the Add-APIs queue's; the workspace bind stays as it was.
		expect(screen.queryByText('Try a request')).not.toBeInTheDocument();
		await user.click(within(group).getByRole('radio', { name: /Custom rules/ }));
		expect(screen.queryByLabelText('Request path')).not.toBeInTheDocument();

		await user.click(within(group).getByRole('radio', { name: /Full access/ }));
		await bind(user);
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0].body).toEqual([{ effect: 'allow', path: '.*' }]);
	});

	it('while the operations load, the path editor is already in use; the toggle lands in place', async () => {
		serveRules();
		let release!: () => void;
		const gate = new Promise<void>((r) => (release = r));
		worker.use(
			http.get('/apis/stripe/stripe-api/1.0.0/operations', async () => {
				await gate;
				return HttpResponse.json({
					data: [
						{
							operation_id: 'listCharges',
							method: 'GET',
							path: '/v1/charges',
							name: 'List charges',
						},
					],
					has_more: false,
					next_cursor: null,
				});
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(
			<BindAgentDialog
				open
				onClose={() => {}}
				credentials={[CRED]}
				apiLabel="Stripe"
				apiReference={{ vendor: 'stripe', name: 'stripe-api', version: '1.0.0' }}
			/>,
		);
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		const editor = screen.getByTestId('bind-agent-custom-editor');
		const pending = within(editor).getByTestId('bind-agent-actions-pending');
		const path = await within(editor).findByLabelText('Path pattern');
		await user.type(path, '/v1/');
		await settled(path);
		const held = pending.getBoundingClientRect().top;
		const pathTop = path.getBoundingClientRect().top;

		release();
		const build = await within(editor).findByRole('group', { name: 'Build rules' });
		expect(build.getBoundingClientRect().top).toBe(held);
		expect(within(editor).getByLabelText('Path pattern')).toBe(path);
		expect(path.getBoundingClientRect().top).toBe(pathTop);
		expect(path).toHaveValue('/v1/');
	});

	it('Custom rules offers By action only for an API whose operations it can read', async () => {
		serveRules();
		worker.use(
			http.get('/apis/stripe/stripe-api/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'listCharges',
							method: 'GET',
							path: '/v1/charges',
							name: 'List charges',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const user = userEvent.setup();
		const { unmount } = renderWithProviders(
			<BindAgentDialog
				open
				onClose={() => {}}
				credentials={[CRED]}
				apiLabel="Stripe"
				apiReference={{ vendor: 'stripe', name: 'stripe-api', version: '1.0.0' }}
			/>,
		);
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		const editor = screen.getByTestId('bind-agent-custom-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const ops = await within(editor).findByRole('list', { name: 'Operations' });
		await user.click(
			within(
				within(ops).getByRole('group', { name: 'Access for GET /v1/charges' }),
			).getByRole('button', { name: 'Allow' }),
		);
		// The choice is staged: binding asks to apply it first.
		await bind(user);
		const guard = await screen.findByRole('dialog', { name: 'Apply 1 change by action?' });
		expect(guard).toHaveTextContent('apply them before binding');
		expect(ruleCalls).toHaveLength(0);
		await user.click(within(guard).getByRole('button', { name: 'Apply' }));
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0].body).toEqual([{ effect: 'allow', operations: ['listCharges'] }]);
		unmount();

		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		const plain = screen.getByTestId('bind-agent-custom-editor');
		expect(within(plain).queryByRole('group', { name: 'Build rules' })).toBeNull();
		expect(plain).not.toHaveTextContent(/not available/);
		await waitFor(() => expect(within(plain).getByLabelText('Path pattern')).toBeVisible());
	});

	it('Escape on the "Apply changes?" prompt keeps editing — the Bind dialog and its draft stay', async () => {
		serveRules();
		worker.use(
			http.get('/apis/stripe/stripe-api/1.0.0/operations', () =>
				HttpResponse.json({
					data: [
						{
							operation_id: 'listCharges',
							method: 'GET',
							path: '/v1/charges',
							name: 'List charges',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		const onClose = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<BindAgentDialog
				open
				onClose={onClose}
				credentials={[CRED]}
				apiLabel="Stripe"
				apiReference={{ vendor: 'stripe', name: 'stripe-api', version: '1.0.0' }}
			/>,
		);
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		const editor = screen.getByTestId('bind-agent-custom-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const ops = await within(editor).findByRole('list', { name: 'Operations' });
		const allow = within(
			within(ops).getByRole('group', { name: 'Access for GET /v1/charges' }),
		).getByRole('button', { name: 'Allow' });
		await user.click(allow);
		await bind(user);
		const guard = await screen.findByRole('dialog', { name: 'Apply 1 change by action?' });
		// Escape makes the browser fire `cancel` at the topmost open modal — the
		// prompt; a synthetic key press never reaches its close watcher, so fire it
		// the same way.
		guard.dispatchEvent(new Event('cancel', { cancelable: true }));
		await waitFor(() => expect(guard).not.toHaveAttribute('open'));
		// The prompt's own promise: Escape keeps editing. The outer dialog is
		// untouched and the staged choice is still there.
		expect(onClose).not.toHaveBeenCalled();
		expect(screen.getByRole('dialog', { name: 'Bind to an agent' })).toHaveAttribute('open');
		expect(allow).toHaveAttribute('aria-pressed', 'true');
		expect(ruleCalls).toHaveLength(0);
	});

	it('Custom rules needs at least one rule before Bind is enabled', async () => {
		serveRules();
		const user = userEvent.setup();
		renderDialog();
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();

		// With no rules yet, the editor opens straight onto the new-rule form.
		await waitFor(() => expect(screen.getByRole('button', { name: 'DELETE' })).toBeVisible());
		await user.click(screen.getByRole('button', { name: 'DELETE' }));
		await user.click(screen.getByRole('button', { name: 'Add' }));
		// The rules are complete; Bind waits only for how long the access lasts.
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '7d');
		expect(screen.getByTestId('bind-agent-confirm')).toBeEnabled();
		await bind(user);
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
		await user.click(screen.getByRole('radio', { name: /Full access/ }));
		await bind(user);

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
		await user.click(screen.getByRole('radio', { name: /Full access/ }));
		await bind(user);
		expect(await screen.findByTestId('bind-agent-rules-failed')).toBeInTheDocument();
		rerender(ui(false));
		rerender(ui(true));
		await screen.findByTestId('bind-agent-list');
		expect(screen.queryByTestId('bind-agent-rules-failed')).not.toBeInTheDocument();
	});
});

describe('BindAgentDialog — what Full access reaches, and naming the credential', () => {
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

	const allowAll = () => screen.findByRole('radio', { name: /Full access/ });

	it('a pinned credential: any method, any path, no coverage note', async () => {
		const pinned = makeMockCredential({
			credential_id: 'cred_pin',
			name: 'Pinned',
			api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' },
		});
		renderWithProviders(
			<BindAgentDialog open onClose={() => {}} credentials={[pinned]} apiLabel="Stripe" />,
		);
		expect(await allowAll()).toHaveAccessibleDescription('Any method, any path');
		expect(screen.queryByTestId('bind-agent-coverage-note')).not.toBeInTheDocument();
	});

	it('an any-version credential: reaches every version, including later ones', async () => {
		renderWithProviders(
			<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />,
		);
		const radio = await allowAll();
		expect(radio).toHaveAccessibleDescription('Any method, every version — incl. later ones');
		expect(screen.getByTestId('bind-agent-coverage-note')).toHaveTextContent(
			'Covers every version of this API',
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
			'Every API this credential covers, incl. later ones',
		);
		expect(screen.getByTestId('bind-agent-coverage-note')).toHaveTextContent(
			'Covers every API of this vendor',
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
		await user.click(screen.getByRole('radio', { name: /^Read only$/ }));
		await bind(user);
		expect(
			await screen.findByText(`Bound “${picked}” to agent_1, with access rules`, {
				exact: false,
			}),
		).toBeInTheDocument();
	});
});

describe('BindAgentDialog — how long the access lasts', () => {
	const DAY = 86_400_000;
	let bodies: { agent: string; body: Record<string, unknown> }[] = [];
	beforeEach(() => {
		setToken('test-token');
		bodies = [];
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
			http.post('/agents/:id/credentials', async ({ params, request }) => {
				bodies.push({
					agent: String(params.id),
					body: (await request.json()) as Record<string, unknown>,
				});
				return HttpResponse.json({}, { status: 201 });
			}),
			http.put('/credentials/:cid/agents/:aid/permissions', () =>
				HttpResponse.json({ data: [] }),
			),
		);
	});

	async function ready(user: ReturnType<typeof userEvent.setup>): Promise<void> {
		await user.click(await screen.findByRole('checkbox', { name: 'agent_1' }));
		await user.click(screen.getByRole('radio', { name: /^Read only$/ }));
	}

	/** Bound rows with an end date (and, optionally, paused). */
	function serveBound(
		pickable: string[],
		bound: { id: string; expiresAt: string | null; suspended?: boolean }[],
	): void {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({
					data: pickable.map(pickerAgent),
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials/:cid/agents', () =>
				HttpResponse.json({
					data: bound.map((b) => ({
						...boundAgent(b.id),
						suspended: b.suspended ?? false,
						expires_at: b.expiresAt,
						expired: b.expiresAt != null && Date.parse(b.expiresAt) <= Date.now(),
					})),
					has_more: false,
					next_cursor: null,
				}),
			),
		);
	}

	it("says what each bound agent's binding is: expired, suspended, ending or open", async () => {
		const past = new Date(Date.now() - DAY).toISOString();
		serveBound(
			['agent_1', 'agent_expired', 'agent_paused', 'agent_soon', 'agent_open'],
			[
				{ id: 'agent_expired', expiresAt: past },
				// Paused and lapsed: the pause is what to fix first, as the broker says.
				{ id: 'agent_paused', expiresAt: past, suspended: true },
				{
					id: 'agent_soon',
					expiresAt: new Date(Date.now() + 3 * DAY + 60_000).toISOString(),
				},
				{ id: 'agent_open', expiresAt: null },
			],
		);
		renderDialog();

		await screen.findByRole('checkbox', { name: 'agent_expired' });
		const note = (name: string): string | null =>
			within(optionFor(name)).getByTestId('bind-agent-bound-note').textContent;
		await waitFor(() => expect(note('agent_expired')).toBe('Bound · expired'));
		expect(note('agent_paused')).toBe('Bound · suspended');
		expect(note('agent_soon')).toBe('Already bound · ends in 3d');
		expect(note('agent_open')).toBe('Already bound');
		expect(
			within(optionFor('agent_1')).queryByTestId('bind-agent-bound-note'),
		).not.toBeInTheDocument();
		// Still not bindable again (a 409): renewing is the way back.
		expect(screen.getByRole('checkbox', { name: 'agent_expired' })).toBeDisabled();
		expect(screen.getByTestId('bind-agent-expired-note')).toHaveTextContent(
			"One agent's access through this credential has expired. Binding again won't restore it: renew it from the agent's page or from the credential.",
		);
	});

	it('every agent bound, one of them expired: says to renew, not bind', async () => {
		serveBound(
			['agent_expired'],
			[{ id: 'agent_expired', expiresAt: new Date(Date.now() - DAY).toISOString() }],
		);
		renderDialog();

		expect(await screen.findByTestId('bind-agent-all-bound')).toBeInTheDocument();
		expect(screen.getByTestId('bind-agent-expired-note')).toHaveTextContent(
			/One agent's access through this credential has expired\./,
		);
	});

	it('preselects no duration, and a chosen one becomes the end date at Bind', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<>
				<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />
				<Toaster />
			</>,
		);
		await ready(user);
		const preset = screen.getByTestId('bind-agent-duration-preset') as HTMLSelectElement;
		expect(preset.value).toBe('');
		expect(screen.getByTestId('bind-agent-access-summary')).toHaveTextContent(
			/pick Custom… and then No expiry/,
		);
		// Rules and agents are set, but Bind waits for a duration.
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();

		await user.selectOptions(preset, '30d');
		const before = Date.now();
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(bodies).toHaveLength(1));
		const at = Date.parse(String(bodies[0].body.expires_at));
		expect(at).toBeGreaterThanOrEqual(before + 30 * DAY);
		expect(at).toBeLessThanOrEqual(Date.now() + 30 * DAY);
		expect(screen.queryByTestId('no-expiry-warning')).not.toBeInTheDocument();
		expect(await screen.findByText(/with access rules, until /)).toBeInTheDocument();
	});

	it('an invalid custom amount blocks Bind', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), 'custom');
		await user.clear(screen.getByTestId('bind-agent-duration-amount'));
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
		await user.type(screen.getByTestId('bind-agent-duration-amount'), '12');
		expect(screen.getByTestId('bind-agent-confirm')).toBeEnabled();
	});

	it('an end date the server refused says why, not "Unprocessable Entity"', async () => {
		worker.use(
			http.post('/agents/:id/credentials', () =>
				HttpResponse.json(
					{
						detail: [
							{
								type: 'value_error',
								loc: ['body', 'expires_at'],
								msg: 'Value error, expires_at must be in the future',
							},
						],
					},
					{ status: 422 },
				),
			),
		);
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await bind(user, '1h');
		expect(
			await screen.findByText(/That end date had already passed when it reached the server/),
		).toBeVisible();
		expect(screen.queryByText(/Unprocessable/)).not.toBeInTheDocument();
	});

	it('sends an idle limit, and an idle limit alone ends access without a warning', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), 'custom');
		await user.selectOptions(screen.getByTestId('bind-agent-duration-unit'), 'never');
		await user.click(screen.getByText('Also end if unused for'));
		await user.selectOptions(screen.getByTestId('bind-agent-idle-preset'), '30d');
		expect(screen.getByTestId('bind-agent-access-summary')).toHaveTextContent(
			'Access ends if unused for 30 days. An agent that keeps using it keeps access.',
		);

		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(screen.queryByTestId('no-expiry-warning')).not.toBeInTheDocument();
		expect(bodies[0].body).toEqual({
			credential_id: 'cred_big',
			expires_at: null,
			max_idle_period: 'P30D',
		});
	});

	it('warns before an end date or idle limit, below the limits, and only when ticked', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		const warning = (): HTMLElement =>
			within(screen.getByTestId('bind-agent-idle-warning')).getByRole('checkbox');

		// No end date and a three-day idle limit: no warning on offer.
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), 'custom');
		await user.selectOptions(screen.getByTestId('bind-agent-duration-unit'), 'never');
		await user.click(screen.getByText('Also end if unused for'));
		await user.selectOptions(screen.getByTestId('bind-agent-idle-preset'), 'custom');
		await user.clear(screen.getByTestId('bind-agent-idle-amount'));
		await user.type(screen.getByTestId('bind-agent-idle-amount'), '3');
		expect(screen.queryByTestId('bind-agent-idle-warning')).not.toBeInTheDocument();

		// A 14-day idle limit offers it, below the idle row.
		await user.clear(screen.getByTestId('bind-agent-idle-amount'));
		await user.type(screen.getByTestId('bind-agent-idle-amount'), '14');
		expect(warning()).not.toBeChecked();
		expect(
			screen.getByTestId('bind-agent-idle').compareDocumentPosition(warning()) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();

		// An end date under 4 days rules it out, whatever the idle limit.
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '1d');
		expect(screen.queryByTestId('bind-agent-idle-warning')).not.toBeInTheDocument();

		// An end date alone is enough to warn about.
		await user.click(screen.getByText('Also end if unused for'));
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '7d');
		expect(warning()).toBeEnabled();
		await user.click(screen.getByText('Warn me before this access ends'));
		expect(warning()).toBeChecked();
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0].body).toMatchObject({ idle_warning: true });
		expect(bodies[0].body).not.toHaveProperty('max_idle_period');
	});

	it('describes when access ends in one line, below End after one use', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '7d');
		await user.click(screen.getByText('Also end if unused for'));
		await user.click(screen.getByText('End after one use'));

		expect(screen.queryByTestId('bind-agent-duration-hint')).not.toBeInTheDocument();
		expect(screen.queryByTestId('bind-agent-idle-summary')).not.toBeInTheDocument();
		const summary = screen.getByTestId('bind-agent-access-summary');
		expect(summary).toHaveTextContent(
			/^Access ends after one use, around .+, or sooner if unused for 14 days\.$/,
		);
		expect(
			screen.getByTestId('bind-agent-single-use').compareDocumentPosition(summary) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		// The controls point at it.
		expect(screen.getByTestId('bind-agent-duration-preset')).toHaveAttribute(
			'aria-describedby',
			summary.id,
		);
	});

	it('holds the first denied request by default, and sends "Let the agent ask"', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '7d');
		const after = screen.getByTestId('bind-agent-after-expiry') as HTMLSelectElement;
		expect(after.value).toBe('hold');
		expect(screen.getByTestId('bind-agent-after-expiry-hint')).toHaveTextContent(
			'waits for you to renew it',
		);
		await user.selectOptions(after, 'refuse');
		expect(screen.getByTestId('bind-agent-after-expiry-hint')).toHaveTextContent(
			'the agent asks you to renew',
		);
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0].body).toMatchObject({ after_expiry: 'refuse' });
		expect(bodies[0].body).not.toHaveProperty('idle_warning');
	});

	it('sends single use, and single use alone ends access without a warning', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), 'custom');
		await user.selectOptions(screen.getByTestId('bind-agent-duration-unit'), 'never');
		await user.click(screen.getByText('End after one use'));
		expect(screen.getByTestId('bind-agent-access-summary')).toHaveTextContent(
			'Access ends after one use.',
		);

		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(screen.queryByTestId('no-expiry-warning')).not.toBeInTheDocument();
		expect(bodies[0].body).toEqual({
			credential_id: 'cred_big',
			expires_at: null,
			max_quota: 1,
		});
	});

	it('starts on the org default and offers nothing above its maximum', async () => {
		worker.use(
			http.get('/agents/credentials\\:policy', () =>
				HttpResponse.json({
					expires_in: { default: 'P7D', max: 'P30D' },
					idle_period: { default: 'P14D', max: 'P30D' },
					single_use_by_default: false,
				}),
			),
		);
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		const preset = screen.getByTestId('bind-agent-duration-preset') as HTMLSelectElement;
		await waitFor(() => expect(preset.value).toBe('7d'));
		expect(within(preset).getByRole('option', { name: '90 days' })).toBeDisabled();
		// The idle limit is on, on its default, and can't be turned off.
		const idleBox = within(screen.getByTestId('bind-agent-idle-toggle')).getByRole('checkbox');
		expect(idleBox).toBeChecked();
		expect(idleBox).toBeDisabled();
		expect((screen.getByTestId('bind-agent-idle-preset') as HTMLSelectElement).value).toBe(
			'14d',
		);

		// No "No expiry" under a maximum, and a longer custom amount says so.
		await user.selectOptions(preset, 'custom');
		const unit = screen.getByTestId('bind-agent-duration-unit');
		expect(within(unit).queryByRole('option', { name: 'No expiry' })).toBeNull();
		const amount = screen.getByTestId('bind-agent-duration-amount');
		await user.clear(amount);
		await user.type(amount, '45');
		expect(screen.getByTestId('bind-agent-duration-error')).toHaveTextContent(
			'This org allows at most 30 days.',
		);
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});

	it('refuses an idle limit under a day', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), '7d');
		await user.click(screen.getByText('Also end if unused for'));
		await user.selectOptions(screen.getByTestId('bind-agent-idle-preset'), 'custom');
		await user.clear(screen.getByTestId('bind-agent-idle-amount'));
		await user.type(screen.getByTestId('bind-agent-idle-amount'), '12');
		await user.selectOptions(screen.getByTestId('bind-agent-idle-unit'), 'hours');
		expect(screen.getByTestId('bind-agent-idle-error')).toHaveTextContent(
			'Choose between 1 day and 999 days.',
		);
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});

	it('binding with No expiry warns first, and binds nothing until confirmed', async () => {
		const user = userEvent.setup();
		renderDialog();
		await ready(user);
		// No default: nothing chosen is not "no expiry", it is no choice yet.
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();

		await user.selectOptions(screen.getByTestId('bind-agent-duration-preset'), 'custom');
		await user.selectOptions(screen.getByTestId('bind-agent-duration-unit'), 'never');
		await user.click(screen.getByTestId('bind-agent-confirm'));
		const warning = await screen.findByTestId('no-expiry-warning');
		expect(warning).toHaveTextContent(
			'agent_1 will keep this access until someone removes it.',
		);
		// "Set an expiry" is the default action, and takes the operator back to the choice.
		const setExpiry = screen.getByTestId('no-expiry-set-expiry');
		await waitFor(() => expect(setExpiry).toHaveFocus());
		await user.click(setExpiry);
		await waitFor(() => expect(screen.getByTestId('bind-agent-duration-preset')).toHaveFocus());
		expect(bodies).toHaveLength(0);

		await user.click(screen.getByTestId('bind-agent-confirm'));
		await user.click(await screen.findByRole('button', { name: 'Bind without expiry' }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		// A confirmed "No expiry" is said, not left out (an org default would fill a gap).
		expect(bodies[0].body).toEqual({ credential_id: 'cred_big', expires_at: null });
	});
});
