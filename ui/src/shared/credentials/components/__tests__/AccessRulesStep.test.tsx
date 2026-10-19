/**
 * AccessRulesStep — the shared "What can this agent call?" step: presets, the
 * coverage caveat, Custom rules (By action / By path over ONE body), and the tools.
 */
import { useState, type ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { AccessRulesStep } from '@/shared/credentials/components/AccessRulesStep';
import {
	actionRule,
	rulesForPreset,
	type RulesPreset,
	type ScopeReach,
} from '@/shared/credentials/lib/accessPresets';

const API = { vendor: 'acme.com', name: 'main', version: '1.0.0' };

const OPS = [
	{ operation_id: 'listItems', method: 'GET', path: '/items', name: 'List items' },
	{ operation_id: 'createItem', method: 'POST', path: '/items', name: 'Create item' },
];

/** A response held until the test lets it go — to catch the loading states. */
function gated(): { wait: Promise<void>; open: () => void } {
	let open!: () => void;
	const wait = new Promise<void>((r) => (open = r));
	return { wait, open };
}

function stubOperations(): void {
	worker.use(
		http.get('/apis/acme.com/main/1.0.0/operations', () =>
			HttpResponse.json({
				data: [
					{
						operation_id: 'listItems',
						method: 'GET',
						path: '/items',
						name: 'List items',
					},
					{
						operation_id: 'createItem',
						method: 'POST',
						path: '/items',
						name: 'Create item',
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		),
	);
}

function Host({
	reach = 'pinned',
	initial = null,
	initialRules = [],
	tester = false,
	withApi = false,
	extraTools,
	onRules,
	pendingUntilClicked = false,
}: {
	/** Host reports `operationsPending` until its "Stop waiting" is clicked. */
	pendingUntilClicked?: boolean;
	reach?: ScopeReach;
	initial?: RulesPreset | null;
	initialRules?: PermissionRule[];
	tester?: boolean;
	withApi?: boolean;
	extraTools?: ReactNode;
	onRules?: (rules: PermissionRule[]) => void;
}) {
	const [preset, setPreset] = useState<RulesPreset | null>(initial);
	const [rules, setRules] = useState<PermissionRule[]>(initialRules);
	const [waiting, setWaiting] = useState(pendingUntilClicked);
	return (
		<>
			{pendingUntilClicked && (
				<button type="button" onClick={(): void => setWaiting(false)}>
					Stop waiting
				</button>
			)}
			<AccessRulesStep
				reach={reach}
				preset={preset}
				onPresetChange={setPreset}
				customRules={rules}
				onCustomRulesChange={(next): void => {
					setRules(next);
					onRules?.(next);
				}}
				tester={tester}
				apiReference={withApi ? API : null}
				operationsPending={waiting}
				extraTools={extraTools}
				testIdPrefix="t"
			/>
		</>
	);
}

describe('AccessRulesStep', () => {
	beforeEach(async () => {
		await page.viewport(900, 900);
		setToken('test-token');
	});

	it('is one labelled radio group of three presets, least access first, nothing preselected', () => {
		renderWithProviders(<Host />);
		const group = screen.getByRole('radiogroup', { name: 'What can this agent call?' });
		const radios = within(group).getAllByRole('radio');
		const names = ['Read only', 'Full access', 'Custom rules'];
		expect(radios).toHaveLength(names.length);
		names.forEach((name, i) => expect(radios[i]).toHaveAccessibleName(name));
		for (const radio of radios) expect(radio).not.toBeChecked();
		expect(within(group).getByRole('radio', { name: 'Read only' })).toHaveAccessibleDescription(
			'GET requests only',
		);
		expect(
			within(group).queryByRole('radio', { name: /Read and write|Choose actions/ }),
		).toBeNull();
		expect(screen.queryByRole('button', { name: /Rules by path/ })).toBeNull();
	});

	it('Custom rules with no rules opens straight onto the new-rule form, in the inset editor', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host />);
		expect(screen.queryByTestId('t-custom-editor')).toBeNull();

		await user.click(screen.getByRole('radio', { name: /Custom rules/ }));
		const editor = await screen.findByTestId('t-custom-editor');
		expect(editor).toHaveClass('bg-surface-inset');
		expect(within(editor).queryByTestId('rules-empty-state')).toBeNull();
		// It grows open (height + fade), then is there to use.
		await waitFor(() => expect(within(editor).getByLabelText('Path pattern')).toBeVisible());
		expect(within(editor).getByRole('button', { name: 'Add' })).toBeVisible();
		// No tester unless asked for — the workspace bind has none.
		expect(screen.queryByRole('button', { name: /Try a request/ })).toBeNull();

		await user.click(within(editor).getByRole('button', { name: 'Cancel' }));
		expect(within(editor).getByTestId('rules-empty-state')).toBeVisible();
		expect(within(editor).getByRole('button', { name: 'Add rule' })).toBeVisible();
	});

	it('Custom rules with no API to read is the path editor alone: no toggle, no hint', async () => {
		renderWithProviders(<Host initial="custom" />);
		const editor = screen.getByTestId('t-custom-editor');
		expect(editor).toHaveAttribute('data-build', 'path');
		expect(within(editor).queryByRole('group', { name: 'Build rules' })).toBeNull();
		expect(within(editor).queryByRole('button', { name: 'By action' })).toBeNull();
		expect(within(editor).queryByRole('button', { name: 'By path' })).toBeNull();
		expect(editor).not.toHaveTextContent(/not available|list of actions/);
		expect(within(editor).queryByTestId('t-actions-pending')).toBeNull();
		await waitFor(() => expect(within(editor).getByLabelText('Path pattern')).toBeVisible());
	});

	it('offers the toggle, By path first, once the API has operations', async () => {
		stubOperations();
		renderWithProviders(<Host initial="custom" withApi />);
		const editor = screen.getByTestId('t-custom-editor');
		const build = await within(editor).findByRole('group', { name: 'Build rules' });
		expect(within(build).getByRole('button', { name: 'By path' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(within(build).getByRole('button', { name: 'By action' })).toBeEnabled();
		expect(build).not.toHaveAttribute('aria-describedby');
		expect(editor).not.toHaveTextContent(/not available/);
	});

	it.each([
		['is not imported (404)', () => HttpResponse.json({ status: 404 }, { status: 404 })],
		[
			'has no operations',
			() => HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
		],
	])(
		'an API that %s never shows the toggle; the line goes and the editor moves up one row',
		async (_label, respond) => {
			const gate = gated();
			worker.use(
				http.get('/apis/acme.com/main/1.0.0/operations', async () => {
					await gate.wait;
					return respond();
				}),
			);
			// Every toggle that ever mounts, however briefly.
			const seen: string[] = [];
			const observer = new MutationObserver(() => {
				if (document.querySelector('[aria-label="Build rules"]')) seen.push('toggle');
			});
			observer.observe(document.body, { childList: true, subtree: true });
			const user = userEvent.setup();
			try {
				renderWithProviders(<Host initial="custom" withApi />);
				const editor = screen.getByTestId('t-custom-editor');
				const pending = within(editor).getByTestId('t-actions-pending');
				expect(pending).toHaveTextContent("Reading this API's actions…");
				const path = await within(editor).findByLabelText('Path pattern');
				await user.type(path, '/half');
				const before = path.getBoundingClientRect().top;
				const lineRow = pending.getBoundingClientRect().height;

				gate.open();
				await waitFor(() =>
					expect(within(editor).queryByTestId('t-actions-pending')).toBeNull(),
				);
				// The one shift there is: up by the held row (and the gap under it).
				const after = within(editor).getByLabelText('Path pattern');
				expect(after).toBe(path);
				expect(after).toHaveValue('/half');
				expect(before - after.getBoundingClientRect().top).toBeGreaterThanOrEqual(lineRow);
				expect(editor).toHaveAttribute('data-build', 'path');
				expect(editor).not.toHaveTextContent(/not available/);
			} finally {
				observer.disconnect();
			}
			expect(seen).toEqual([]);
		},
	);

	it('a host that stops waiting (the post-import timeout) takes the line away', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" pendingUntilClicked />);
		const editor = screen.getByTestId('t-custom-editor');
		expect(within(editor).getByTestId('t-actions-pending')).toBeVisible();
		await user.type(await within(editor).findByLabelText('Path pattern'), '/kept');
		await user.click(screen.getByRole('button', { name: 'Stop waiting' }));
		expect(within(editor).queryByTestId('t-actions-pending')).toBeNull();
		expect(within(editor).queryByRole('group', { name: 'Build rules' })).toBeNull();
		expect(within(editor).getByLabelText('Path pattern')).toHaveValue('/kept');
	});

	it('the path editor is usable while the operations load: a rule can be added', async () => {
		const gate = gated();
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', async () => {
				await gate.wait;
				return HttpResponse.json({ data: OPS, has_more: false, next_cursor: null });
			}),
		);
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi tester onRules={onRules} />);
		const editor = screen.getByTestId('t-custom-editor');
		expect(within(editor).getByTestId('t-actions-pending')).toBeVisible();
		const path = await within(editor).findByLabelText('Path pattern');
		expect(path).toHaveAttribute('placeholder', '/resource');
		await user.click(path);
		expect(within(editor).queryByRole('listbox')).toBeNull();
		await user.click(within(editor).getByRole('button', { name: 'GET' }));
		await user.type(path, '/v1/');
		await user.click(within(editor).getByRole('button', { name: 'Add' }));
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
		]);
		// Effective access counts against the whole list, so it has no count yet.
		expect(screen.getByRole('button', { name: /Effective access/ })).not.toHaveTextContent(
			/allowed/,
		);
		gate.open();
		await within(editor).findByRole('group', { name: 'Build rules' });
		expect(
			within(editor).getByRole('list', { name: 'Rules, in evaluation order' }),
		).toHaveTextContent('/v1/');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /Effective access/ })).toHaveTextContent(
				'0 of 2 allowed',
			),
		);
	});

	it('the line turns into the toggle in place: nothing below moves, and a half-typed rule survives', async () => {
		const gate = gated();
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', async () => {
				await gate.wait;
				return HttpResponse.json({ data: OPS, has_more: false, next_cursor: null });
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi />);
		const editor = screen.getByTestId('t-custom-editor');
		const pending = within(editor).getByTestId('t-actions-pending');
		expect(pending).toHaveAttribute('role', 'status');
		expect(within(editor).queryByRole('group', { name: 'Build rules' })).toBeNull();
		const path = await within(editor).findByLabelText('Path pattern');
		await waitFor(() => expect(path).toBeVisible());
		await user.click(within(editor).getByRole('button', { name: 'POST' }));
		await user.type(path, '/ite');
		expect(path).toHaveFocus();
		const held = pending.getBoundingClientRect();
		const pathTop = path.getBoundingClientRect().top;

		gate.open();
		const build = await within(editor).findByRole('group', { name: 'Build rules' });
		const landed = build.getBoundingClientRect();
		expect(landed.top).toBe(held.top);
		expect(landed.height).toBe(held.height);
		expect(within(build).getByRole('button', { name: 'By path' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		const after = within(editor).getByLabelText('Path pattern');
		expect(after).toBe(path);
		expect(after.getBoundingClientRect().top).toBe(pathTop);
		expect(after).toHaveValue('/ite');
		expect(within(editor).getByRole('button', { name: 'POST' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		await waitFor(() => expect(after).toHaveAttribute('placeholder', '/items'));
		expect(after).toHaveFocus();
		expect(within(editor).queryByRole('listbox')).toBeNull();
		await user.type(after, 'm');
		expect(within(editor).getByRole('listbox')).toBeVisible();
		await user.keyboard('{Tab}');
		expect(after).toHaveValue('/items');
	});

	it('offers By action from the FIRST page, says it is still loading, and holds back what needs the whole list', async () => {
		const page2 = gated();
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', async ({ request }) => {
				const cursor = new URL(request.url).searchParams.get('cursor');
				if (!cursor) {
					return HttpResponse.json({ data: [OPS[0]], has_more: true, next_cursor: 'p2' });
				}
				await page2.wait;
				return HttpResponse.json({ data: [OPS[1]], has_more: false, next_cursor: null });
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'allow', methods: ['GET'], path: '/nowhere', match_mode: 'exact' },
				]}
			/>,
		);
		const editor = screen.getByTestId('t-custom-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const checklist = within(editor).getByTestId('t-actions');
		expect(checklist).toHaveAttribute('aria-busy', 'true');
		expect(within(checklist).getAllByRole('checkbox')).toHaveLength(1);
		expect(within(checklist).getByLabelText('Filter operations')).toHaveAttribute(
			'placeholder',
			'Filter the 1 loaded so far…',
		);
		expect(within(checklist).getByTestId('t-actions-loading')).toHaveTextContent(
			'Loading more operations — 1 so far. The filter searches only these.',
		);
		await user.type(within(checklist).getByLabelText('Filter operations'), 'create');
		expect(checklist).toHaveTextContent('None of the operations loaded so far match.');
		await user.clear(within(checklist).getByLabelText('Filter operations'));
		await user.click(within(checklist).getAllByRole('checkbox')[0]);
		expect(within(checklist).getByText('1 chosen')).toBeInTheDocument();
		// Nothing is judged against a partial list.
		expect(screen.getByRole('button', { name: /Effective access/ })).not.toHaveTextContent(
			/allowed/,
		);
		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		expect(within(editor).queryByLabelText('No operations affected')).toBeNull();

		page2.open();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /Effective access/ })).toHaveTextContent(
				/of 2 allowed/,
			),
		);
		expect(within(editor).getByLabelText('No operations affected')).toBeInTheDocument();
		await user.click(within(editor).getByRole('button', { name: 'By action' }));
		const whole = within(editor).getByTestId('t-actions');
		expect(whole).not.toHaveAttribute('aria-busy');
		expect(within(whole).queryByTestId('t-actions-loading')).toBeNull();
		expect(within(whole).getAllByRole('checkbox')).toHaveLength(2);
		expect(within(whole).getByLabelText('Filter operations')).toHaveAttribute(
			'placeholder',
			'Filter 2 operations…',
		);
		expect(within(whole).getByText('1 chosen')).toBeInTheDocument();
	});

	it('an action rule saved earlier still shows on an API with no actions — Remove, but no Edit', async () => {
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				onRules={onRules}
				initialRules={[
					{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
					{
						effect: 'allow',
						methods: null,
						path: null,
						operations: ['listItems', 'createItem'],
					},
				]}
			/>,
		);
		const editor = screen.getByTestId('t-custom-editor');
		expect(within(editor).queryByRole('group', { name: 'Build rules' })).toBeNull();
		// The never-hidden contract outranks the missing checklist: the row still shows.
		const row = within(editor).getByTestId('t-action-rule');
		expect(row).toHaveTextContent('#2');
		expect(row).toHaveTextContent('2 actions');
		expect(row).toHaveTextContent('listItems');
		expect(row).toHaveTextContent('createItem');
		expect(
			within(editor).getByRole('list', { name: 'Rules, in evaluation order' }),
		).toHaveTextContent('/v1/');
		expect(within(row).queryByRole('button', { name: 'Edit' })).toBeNull();
		await user.click(within(row).getByRole('button', { name: 'Remove the actions rule' }));
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
		]);
		expect(within(editor).queryByTestId('t-action-rule')).toBeNull();
	});

	it('By action ticks operations into one operation-id rule in the same saved list', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi onRules={onRules} />);
		const editor = screen.getByTestId('t-custom-editor');
		const byAction = await within(editor).findByRole('button', { name: 'By action' });
		expect(byAction).toBeEnabled();

		await user.click(byAction);
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		await user.click(within(list).getByRole('checkbox', { name: /List items/ }));
		await user.click(within(list).getByRole('checkbox', { name: /Create item/ }));
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: null, path: null, operations: ['listItems', 'createItem'] },
		]);
		expect(within(editor).getByText('2 chosen')).toBeInTheDocument();

		await user.click(within(editor).getByRole('button', { name: 'Clear' }));
		expect(onRules).toHaveBeenLastCalledWith([]);
	});

	it('the toggle picks the editor, never a filter: both halves stay on screen', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi onRules={onRules} />);
		const editor = screen.getByTestId('t-custom-editor');

		await waitFor(() =>
			expect(within(editor).getByRole('button', { name: 'GET' })).toBeVisible(),
		);
		await user.click(within(editor).getByRole('button', { name: 'GET' }));
		await user.type(within(editor).getByLabelText('Path pattern'), '/v1/');
		await user.click(within(editor).getByRole('button', { name: 'Add' }));

		const byAction = await within(editor).findByRole('button', { name: 'By action' });
		await user.click(byAction);
		const ops = await within(editor).findByRole('list', { name: 'Operations' });
		await user.click(within(ops).getByRole('checkbox', { name: /List items/ }));

		// Nothing that will be saved is off-screen: the path rule shows under By action.
		const pathList = within(editor).getByRole('list', { name: 'Rules, in evaluation order' });
		expect(within(pathList).getAllByRole('listitem')).toHaveLength(1);
		expect(pathList).toHaveTextContent('/v1/');
		expect(within(editor).getByText('1 chosen')).toBeInTheDocument();

		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		const actionRow = within(editor).getByTestId('t-action-rule');
		expect(actionRow).toHaveTextContent('1 action');
		expect(actionRow).toHaveTextContent('listItems');
		expect(
			within(
				within(editor).getByRole('list', { name: 'Rules, in evaluation order' }),
			).getAllByRole('listitem'),
		).toHaveLength(1);

		// Path rule first, so its row number matches the dry run's.
		const emitted = onRules.mock.lastCall?.[0] as PermissionRule[];
		expect(emitted).toEqual([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
		expect(rulesForPreset('custom', emitted)).toEqual([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
			{ effect: 'allow', operations: ['listItems'] },
		]);
	});

	it("editing a path row leaves the action rule's operation ids alone", async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				onRules={onRules}
				initialRules={[
					{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
					{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
				]}
			/>,
		);
		const editor = screen.getByTestId('t-custom-editor');
		await user.click(within(editor).getByRole('button', { name: 'Edit rule' }));
		await user.click(within(editor).getByRole('button', { name: 'POST' }));
		await user.click(within(editor).getByRole('button', { name: 'Save' }));

		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['GET', 'POST'], path: '/v1/', match_mode: 'prefix' },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
		expect(within(editor).getByTestId('t-action-rule')).toHaveTextContent('listItems');
	});

	it('the action rule can be removed, and Edit takes the operator to the checklist', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				onRules={onRules}
				initialRules={[
					{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
				]}
			/>,
		);
		const editor = screen.getByTestId('t-custom-editor');
		// Edit is offered once the checklist it returns to is known to exist.
		await user.click(
			await within(within(editor).getByTestId('t-action-rule')).findByRole('button', {
				name: 'Edit',
			}),
		);
		const ops = await within(editor).findByRole('list', { name: 'Operations' });
		expect(within(ops).getByRole('checkbox', { name: /List items/ })).toBeChecked();

		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		await user.click(
			within(within(editor).getByTestId('t-action-rule')).getByRole('button', {
				name: 'Remove the actions rule',
			}),
		);
		expect(onRules).toHaveBeenLastCalledWith([]);
	});

	it('the presets are the shared radio-card group — Home and End jump its ends', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host />);
		const group = screen.getByTestId('t-rules-preset');
		const radios = within(group).getAllByRole('radio');
		radios[2].focus();

		await user.keyboard('{Home}');
		expect(radios[0]).toBeChecked();
		expect(radios[0]).toHaveFocus();
		// End lands on Custom rules (its editor then takes focus; the selection is what's pinned).
		await user.keyboard('{End}');
		expect(radios[2]).toBeChecked();
		radios[0].focus();
		await user.keyboard('{ArrowUp}');
		expect(radios[2]).toBeChecked();
	});

	it('stays flat: no rule between the preset rows, and no fill the parent already gives', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi />);

		// The presets are a plain list: the group paints no fill or border (`Card.tsx`).
		const group = screen.getByTestId('t-rules-preset');
		expect(group.className).toContain('bg-transparent');
		expect(group.className).toContain('divide-y-0');
		const groupStyle = getComputedStyle(group);
		expect(groupStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');
		for (const radio of within(group).getAllByRole('radio')) {
			expect(getComputedStyle(radio).borderBottomWidth).toBe('0px');
		}

		// The checklist has no fill: the editor panel is the one surface grouping it.
		const editor = screen.getByTestId('t-custom-editor');
		const editorBg = getComputedStyle(editor).backgroundColor;
		const byAction = await within(editor).findByRole('button', { name: 'By action' });
		await user.click(byAction);
		const checklist = await screen.findByTestId('t-actions');
		expect(getComputedStyle(checklist).backgroundColor).toBe('rgba(0, 0, 0, 0)');
		expect(editorBg).not.toBe('rgba(0, 0, 0, 0)');
	});

	it('a preset with no description carries no dangling aria-describedby', () => {
		renderWithProviders(<Host />);
		for (const radio of within(screen.getByTestId('t-rules-preset')).getAllByRole('radio')) {
			const ref = radio.getAttribute('aria-describedby');
			if (ref) expect(document.getElementById(ref)).not.toBeNull();
		}
	});

	it('a toggle with nothing to switch to carries no explanation to point at', () => {
		renderWithProviders(<Host initial="custom" />);
		const editor = screen.getByTestId('t-custom-editor');
		expect(within(editor).queryByRole('group', { name: 'Build rules' })).toBeNull();
		expect(within(editor).queryByTestId('t-actions-unavailable')).toBeNull();
		for (const el of editor.querySelectorAll('[aria-describedby]')) {
			const ref = el.getAttribute('aria-describedby') ?? '';
			for (const id of ref.split(/\s+/).filter(Boolean)) {
				expect(document.getElementById(id)).not.toBeNull();
			}
		}
	});

	it('the tools are stacked rows in a fixed order, each opening its panel under it', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="read"
				tester
				withApi
				extraTools={<button type="button">Escape</button>}
			/>,
		);
		const tools = screen.getByTestId('t-tools');
		const rows = within(tools).getAllByRole('listitem');
		// Fixed order, never merged: Effective access, Try a request, then the host's row.
		expect(rows).toHaveLength(3);
		expect(within(rows[0]).getByRole('button', { name: /Effective access/ })).toBeVisible();
		expect(within(rows[1]).getByRole('button', { name: /Try a request/ })).toBeVisible();
		expect(within(rows[2]).getByRole('button', { name: 'Escape' })).toBeVisible();
		// A preset reads the API's operations only once a tool is opened.
		expect(
			within(rows[0]).getByRole('button', { name: /Effective access/ }),
		).not.toHaveTextContent(/allowed/);

		await user.click(within(rows[1]).getByRole('button', { name: /Try a request/ }));
		await waitFor(() =>
			expect(
				within(rows[0]).getByRole('button', { name: /Effective access/ }),
			).toHaveTextContent('1 of 2 allowed'),
		);
		expect(within(rows[1]).getByTestId('t-tester')).toBeInTheDocument();
		expect(within(rows[1]).getByRole('button', { name: /Try a request/ })).toHaveAttribute(
			'aria-expanded',
			'true',
		);
	});

	it('warns when a path deny, evaluated first, can block the chosen actions', () => {
		const deny: PermissionRule = { effect: 'deny', methods: ['DELETE'], path: '.*' };
		const { unmount } = renderWithProviders(
			<Host initial="custom" initialRules={[deny, actionRule(['repos/delete'])]} />,
		);
		expect(screen.getByTestId('t-actions-shadowed')).toHaveTextContent(/evaluated first/);
		unmount();
		renderWithProviders(
			<Host initial="custom" initialRules={[actionRule(['repos/delete'])]} />,
		);
		expect(screen.queryByTestId('t-actions-shadowed')).toBeNull();
	});

	it('says what Full access reaches and notes coverage past a pinned API', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host reach="vendor-wide" />);
		expect(screen.getByRole('radio', { name: /Full access/ })).toHaveAccessibleDescription(
			'Every API this credential covers, incl. later ones',
		);
		const note = screen.getByTestId('t-coverage-note');
		expect(note).toHaveTextContent('Covers every API of this vendor');
		expect(note).not.toHaveTextContent('including ones added later');
		await user.click(within(note).getByRole('button', { name: /Details/ }));
		await waitFor(() =>
			expect(
				screen.getByText(/these rules apply to all of them, including ones added later/),
			).toBeVisible(),
		);
	});

	it('a pinned credential has no coverage caveat', () => {
		renderWithProviders(<Host reach="pinned" />);
		expect(screen.queryByTestId('t-coverage-note')).toBeNull();
	});

	it('the inline tester follows the preset without being asked again', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" tester />);
		await user.click(screen.getByRole('button', { name: /Try a request/ }));
		const tester = screen.getByTestId('t-tester');
		await user.selectOptions(within(tester).getByLabelText('HTTP method'), 'POST');
		await user.type(within(tester).getByLabelText('Request path'), '/items');
		await user.click(within(tester).getByRole('button', { name: 'Test' }));
		expect(await within(tester).findByTestId('rule-verdict')).toHaveTextContent(
			'Denied POST /items — no rule matched (default deny)',
		);

		await user.click(screen.getByRole('radio', { name: /Full access/ }));
		await waitFor(() =>
			expect(within(tester).getByTestId('rule-verdict')).toHaveTextContent(
				'Allowed POST /items — allowed by “Full access”',
			),
		);
		await user.click(screen.getByRole('radio', { name: /Read only/ }));
		await waitFor(() =>
			expect(within(tester).getByTestId('rule-verdict')).toHaveTextContent(/^Denied/),
		);
		// Announced politely for screen-reader users as it changes.
		expect(within(tester).getByRole('status')).toHaveTextContent('POST /items: denied');
	});

	it('passes an accessibility audit with the editor and tester open', async () => {
		const { container } = renderWithProviders(<Host initial="custom" tester />);
		await checkA11y(container);
	});
});
