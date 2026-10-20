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

/** The Allow · Ask · Deny choice for one action, by its method and path. */
function accessFor(scope: HTMLElement, action: string): HTMLElement {
	return within(scope).getByRole('group', { name: `Access for ${action}` });
}

/** Which of the row's choices is pressed: 'Allow' / 'Ask' / 'Deny', or 'unset'. */
function pressed(scope: HTMLElement, action: string): string {
	const on = within(accessFor(scope, action))
		.getAllByRole('button')
		.filter((b) => b.getAttribute('aria-pressed') === 'true');
	return on.length === 0 ? 'unset' : (on[0].textContent ?? '');
}

const accessGroups = (scope: HTMLElement) =>
	within(scope).getAllByRole('group', { name: /^Access for / });

/** Set one action's access by clicking its segment. */
async function setAccess(
	user: ReturnType<typeof userEvent.setup>,
	scope: HTMLElement,
	action: string,
	to: 'Allow' | 'Ask' | 'Deny',
): Promise<void> {
	await user.click(within(accessFor(scope, action)).getByRole('button', { name: to }));
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

	it('is one labelled radio group of four presets, least access first, nothing preselected', async () => {
		const { container } = renderWithProviders(<Host />);
		const group = screen.getByRole('radiogroup', { name: 'What can this agent call?' });
		const radios = within(group).getAllByRole('radio');
		const names = [
			'Read only',
			'Read only, ask before anything else',
			'Full access',
			'Custom rules',
		];
		expect(radios).toHaveLength(names.length);
		names.forEach((name, i) => expect(radios[i]).toHaveAccessibleName(name));
		for (const radio of radios) expect(radio).not.toBeChecked();
		expect(within(group).getByRole('radio', { name: 'Read only' })).toHaveAccessibleDescription(
			'GET requests only',
		);
		expect(
			within(group).getByRole('radio', { name: 'Read only, ask before anything else' }),
		).toHaveAccessibleDescription('GET requests run; any other call waits for approval');
		expect(
			within(group).queryByRole('radio', { name: /Read and write|Choose actions/ }),
		).toBeNull();
		expect(screen.queryByRole('button', { name: /Rules by path/ })).toBeNull();
		await checkA11y(container);
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
				'0 allowed · 0 ask · 2 denied',
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
		expect(accessGroups(checklist)).toHaveLength(1);
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
		await setAccess(user, checklist, 'GET /items', 'Allow');
		expect(within(checklist).getByTestId('t-actions-count')).toHaveTextContent('1 allowed');
		// Nothing is judged against a partial list.
		expect(screen.getByRole('button', { name: /Effective access/ })).not.toHaveTextContent(
			/allowed/,
		);
		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		expect(within(editor).queryByLabelText('No operations affected')).toBeNull();

		page2.open();
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /Effective access/ })).toHaveTextContent(
				/1 allowed · 0 ask · 1 denied/,
			),
		);
		expect(within(editor).getByLabelText('No operations affected')).toBeInTheDocument();
		await user.click(within(editor).getByRole('button', { name: 'By action' }));
		const whole = within(editor).getByTestId('t-actions');
		expect(whole).not.toHaveAttribute('aria-busy');
		expect(within(whole).queryByTestId('t-actions-loading')).toBeNull();
		expect(accessGroups(whole)).toHaveLength(2);
		expect(within(whole).getByLabelText('Filter operations')).toHaveAttribute(
			'placeholder',
			'Filter 2 operations…',
		);
		expect(within(whole).getByTestId('t-actions-count')).toHaveTextContent('1 allowed');
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
		// No list to name them from: said plainly, the ids only behind "Details".
		expect(row).toHaveTextContent("this API's list of actions isn't available");
		expect(within(row).queryByText('listItems')).toBeNull();
		await user.click(within(row).getByRole('button', { name: 'Details' }));
		const ids = await within(row).findByRole('list', { name: 'Operation ids' });
		expect(ids).toHaveTextContent('listItems');
		expect(ids).toHaveTextContent('createItem');
		expect(
			within(editor).getByRole('list', { name: 'Rules, in evaluation order' }),
		).toHaveTextContent('/v1/');
		expect(within(row).queryByRole('button', { name: 'Edit' })).toBeNull();
		await user.click(
			within(row).getByRole('button', { name: 'Remove the Allow actions rule' }),
		);
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
		]);
		expect(within(editor).queryByTestId('t-action-rule')).toBeNull();
	});

	it('By action sets operations to Allow into one operation-id rule in the same saved list', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi onRules={onRules} />);
		const editor = screen.getByTestId('t-custom-editor');
		const byAction = await within(editor).findByRole('button', { name: 'By action' });
		expect(byAction).toBeEnabled();

		await user.click(byAction);
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		await setAccess(user, list, 'GET /items', 'Allow');
		await setAccess(user, list, 'POST /items', 'Allow');
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: null, path: null, operations: ['listItems', 'createItem'] },
		]);
		expect(within(editor).getByTestId('t-actions-count')).toHaveTextContent('2 allowed');

		await user.click(
			within(within(editor).getByTestId('t-actions-bulk')).getByRole('button', {
				name: 'Clear',
			}),
		);
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
		await setAccess(user, ops, 'GET /items', 'Allow');

		// Nothing that will be saved is off-screen: the path rule shows under By action.
		const pathList = within(editor).getByRole('list', { name: 'Rules, in evaluation order' });
		expect(within(pathList).getAllByRole('listitem')).toHaveLength(1);
		expect(pathList).toHaveTextContent('/v1/');
		expect(within(editor).getByTestId('t-actions-count')).toHaveTextContent('1 allowed');

		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		const actionRow = within(editor).getByTestId('t-action-rule');
		expect(actionRow).toHaveTextContent('1 action');
		const named = within(actionRow).getByRole('list', { name: 'Actions' });
		expect(named).toHaveTextContent('GET/itemsList items');
		expect(named).not.toHaveTextContent('listItems');
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
		expect(within(editor).getByTestId('t-action-rule')).toHaveTextContent('List items');
	});

	it('By path names each chosen action as the checklist does — method, path, summary', async () => {
		stubOperations();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{
						effect: 'allow',
						methods: null,
						path: null,
						operations: ['createItem', 'listItems', 'gone'],
					},
				]}
			/>,
		);
		const row = await screen.findByTestId('t-action-rule');
		const named = await within(row).findByRole('list', { name: 'Actions' });
		const items = within(named).getAllByRole('listitem');
		expect(items.map((li) => li.textContent)).toEqual([
			'GET/itemsList items',
			'POST/itemsCreate item',
			'An action not in this version of the API',
		]);
		// The method badge carries its text; the id is only the row's title.
		expect(within(items[0]).getByText('GET')).toHaveAttribute('data-method', 'GET');
		expect(items[0]).toHaveAttribute('title', 'listItems');
		expect(row).toHaveTextContent('One action is not in this version of the API.');
		expect(row).not.toHaveTextContent('createItem');
		expect(row).not.toHaveTextContent('gone');
		await checkA11y(row);
	});

	it('while the actions load, the row shows a count and a pending line, not ids', async () => {
		const gate = gated();
		worker.use(
			http.get('/apis/acme.com/main/1.0.0/operations', async () => {
				await gate.wait;
				return HttpResponse.json({ data: OPS, has_more: false, next_cursor: null });
			}),
		);
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
				]}
			/>,
		);
		const row = screen.getByTestId('t-action-rule');
		expect(row).toHaveTextContent('1 action');
		expect(within(row).getByRole('status')).toHaveTextContent(
			"Reading this API's actions — 1 action…",
		);
		expect(row).not.toHaveTextContent('listItems');
		gate.open();
		expect(await within(row).findByText('List items')).toBeInTheDocument();
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
		expect(pressed(ops, 'GET /items')).toBe('Allow');
		expect(pressed(ops, 'POST /items')).toBe('unset');

		await user.click(within(editor).getByRole('button', { name: 'By path' }));
		await user.click(
			within(within(editor).getByTestId('t-action-rule')).getByRole('button', {
				name: 'Remove the Allow actions rule',
			}),
		);
		expect(onRules).toHaveBeenLastCalledWith([]);
	});

	it('By action: each row is Allow · Ask · Deny, unset by default; clicking the pressed one unsets it', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<Host initial="custom" withApi onRules={onRules} />,
		);
		const editor = screen.getByTestId('t-custom-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		// No "Off" option: unset is nothing pressed, the default for an action in no rule.
		for (const action of ['GET /items', 'POST /items']) {
			expect(
				within(accessFor(list, action))
					.getAllByRole('button')
					.map((b) => b.textContent),
			).toEqual(['Allow', 'Ask', 'Deny']);
			expect(pressed(list, action)).toBe('unset');
		}
		expect(within(editor).getByTestId('t-actions-count')).toHaveTextContent('None set');

		await setAccess(user, list, 'GET /items', 'Allow');
		await setAccess(user, list, 'POST /items', 'Ask');
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'require-approval', methods: null, path: null, operations: ['createItem'] },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
		expect(within(editor).getByTestId('t-actions-count')).toHaveTextContent(
			'1 allowed · 1 ask',
		);
		await checkA11y(container);

		// Ask → Deny: Deny saves ahead of everything.
		await setAccess(user, list, 'POST /items', 'Deny');
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'deny', methods: null, path: null, operations: ['createItem'] },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
		expect(within(editor).getByTestId('t-actions-count')).toHaveTextContent(
			'1 allowed · 1 denied',
		);
		// Clicking the pressed choice unsets it — never into a Deny.
		await setAccess(user, list, 'POST /items', 'Deny');
		expect(pressed(list, 'POST /items')).toBe('unset');
		await setAccess(user, list, 'GET /items', 'Allow');
		expect(onRules).toHaveBeenLastCalledWith([]);
	});

	it('the row control is one tab stop; arrows move within it, Space sets, Delete unsets', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" withApi onRules={onRules} />);
		const editor = screen.getByTestId('t-custom-editor');
		await user.click(await within(editor).findByRole('button', { name: 'By action' }));
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		const [allow, ask] = within(accessFor(list, 'GET /items')).getAllByRole('button');
		expect(allow).toHaveAttribute('tabindex', '0');
		expect(ask).toHaveAttribute('tabindex', '-1');
		allow.focus();
		await user.keyboard('{ArrowRight}');
		expect(ask).toHaveFocus();
		expect(pressed(list, 'GET /items')).toBe('unset');
		await user.keyboard(' ');
		expect(pressed(list, 'GET /items')).toBe('Ask');
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'require-approval', methods: null, path: null, operations: ['listItems'] },
		]);
		await user.keyboard('{Backspace}');
		expect(pressed(list, 'GET /items')).toBe('unset');
		expect(onRules).toHaveBeenLastCalledWith([]);
		// Tab leaves the row's control for the next row's.
		await user.tab();
		expect(
			within(accessFor(list, 'POST /items')).getByRole('button', { name: 'Allow' }),
		).toHaveFocus();
	});

	it('the filter narrows the rows, and "Set … to" acts on just the filtered ones — Clear unsets', async () => {
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
		await user.click(await within(editor).findByRole('button', { name: 'Edit' }));
		const checklist = within(editor).getByTestId('t-actions');
		const bulk = within(checklist).getByTestId('t-actions-bulk');
		expect(bulk).toHaveTextContent('Set all 2 to');
		expect(
			within(bulk)
				.getAllByRole('button')
				.map((b) => b.textContent),
		).toEqual(['Allow', 'Ask', 'Deny', 'Clear']);
		await user.type(within(checklist).getByLabelText('Filter operations'), 'create');
		expect(accessGroups(checklist)).toHaveLength(1);
		expect(bulk).toHaveTextContent('Set the 1 shown to');
		expect(bulk).toHaveAccessibleName('Set the 1 filtered operations');
		await user.click(within(bulk).getByRole('button', { name: 'Deny' }));
		// The hidden row keeps its Allow; only the filtered row moved.
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'deny', methods: null, path: null, operations: ['createItem'] },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
		await user.clear(within(checklist).getByLabelText('Filter operations'));
		await user.click(within(bulk).getByRole('button', { name: 'Clear' }));
		expect(onRules).toHaveBeenLastCalledWith([]);
		expect(within(checklist).getByTestId('t-actions-count')).toHaveTextContent('None set');
		await user.click(within(bulk).getByRole('button', { name: 'Allow' }));
		expect(within(checklist).getByTestId('t-actions-count')).toHaveTextContent('2 allowed');
	});

	it('saved rules load onto the rows; an id in two rules shows the earlier rule (first match wins)', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					// Hand-edited: createItem is in both; the Ask rule comes first.
					{
						effect: 'require-approval',
						methods: null,
						path: null,
						operations: ['createItem'],
					},
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
		await user.click(
			await within(within(editor).getByTestId('t-action-rule')).findByRole('button', {
				name: 'Edit',
			}),
		);
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		expect(pressed(list, 'GET /items')).toBe('Allow');
		expect(pressed(list, 'POST /items')).toBe('Ask');
	});

	it('By path shows the Deny rule row above the path rules, Ask and Allow below, numbered as saved', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'deny', methods: null, path: null, operations: ['createItem'] },
					{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
					{
						effect: 'require-approval',
						methods: null,
						path: null,
						operations: ['listItems'],
					},
				]}
			/>,
		);
		const editor = screen.getByTestId('t-custom-editor');
		const deny = await within(editor).findByTestId('t-action-rule-deny');
		const pathList = within(editor).getByRole('list', { name: 'Rules, in evaluation order' });
		const ask = within(editor).getByTestId('t-action-rule-ask');
		expect(deny).toHaveTextContent('#1deny1 action');
		expect(await within(deny).findByText('Create item')).toBeInTheDocument();
		expect(pathList).toHaveTextContent('#2');
		expect(ask).toHaveTextContent('#3ask1 action');
		const follows = (a: Node, b: Node) =>
			Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
		expect(follows(deny, pathList)).toBe(true);
		expect(follows(pathList, ask)).toBe(true);
		// Edit lands on By action with each row as saved.
		await user.click(within(deny).getByRole('button', { name: 'Edit' }));
		const list = await within(editor).findByRole('list', { name: 'Operations' });
		expect(pressed(list, 'POST /items')).toBe('Deny');
		expect(pressed(list, 'GET /items')).toBe('Ask');
	});

	it('a Deny action rule saved after the path rules loads as Deny, with a notice that saving moves it', async () => {
		stubOperations();
		const onRules = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				onRules={onRules}
				initialRules={[
					{ effect: 'allow', methods: ['POST'], path: '/items', match_mode: 'prefix' },
					{ effect: 'deny', methods: null, path: null, operations: ['createItem'] },
				]}
			/>,
		);
		const notice = screen.getByTestId('t-deny-moves-ahead');
		expect(notice).toHaveAttribute('role', 'status');
		expect(notice).toHaveTextContent(
			'A Deny actions rule sits after the path rules. Saving moves it ahead of them',
		);
		expect(screen.getByTestId('t-action-rule-deny')).toHaveTextContent('#1deny');
		// Any edit rewrites the list in saved order, and the notice goes with it.
		await user.click(screen.getByRole('button', { name: 'Remove the Deny actions rule' }));
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['POST'], path: '/items', match_mode: 'prefix' },
		]);
		expect(screen.queryByTestId('t-deny-moves-ahead')).toBeNull();
	});

	it('a Deny action beats a path rule that allows it — in Effective access and in Try a request', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				tester
				initialRules={[
					{ effect: 'deny', methods: null, path: null, operations: ['createItem'] },
					{ effect: 'allow', path: '/items', match_mode: 'prefix' },
				]}
			/>,
		);
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /Effective access/ })).toHaveTextContent(
				'1 allowed · 0 ask · 1 denied',
			),
		);
		await user.click(screen.getByRole('button', { name: /Try a request/ }));
		const tester = screen.getByTestId('t-tester');
		await user.selectOptions(within(tester).getByLabelText('HTTP method'), 'POST');
		await user.type(within(tester).getByLabelText('Request path'), '/items');
		await user.click(within(tester).getByRole('button', { name: /operation id/ }));
		await user.type(within(tester).getByPlaceholderText('operationId'), 'createItem');
		await user.click(within(tester).getByRole('button', { name: 'Test' }));
		const verdict = await within(tester).findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Denied');
		expect(verdict).toHaveTextContent('#1');
	});

	it('warns when a path deny ahead blocks an Allow action — the real conflict, not a vague hint', async () => {
		stubOperations();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'deny', methods: ['POST'], path: '/items', match_mode: 'prefix' },
					{
						effect: 'allow',
						methods: null,
						path: null,
						operations: ['createItem', 'listItems'],
					},
				]}
			/>,
		);
		const warning = await screen.findByTestId('t-actions-shadowed');
		expect(warning).toHaveTextContent('a deny there blocks one Allow or Ask action.');
	});

	it('By path shows each effect as its own rule row, Ask first, with readable actions', async () => {
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
					{
						effect: 'require-approval',
						methods: null,
						path: null,
						operations: ['createItem'],
					},
					{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
				]}
			/>,
		);
		const ask = await screen.findByTestId('t-action-rule-ask');
		const allow = screen.getByTestId('t-action-rule');
		expect(ask).toHaveTextContent('#2ask1 action');
		expect(await within(ask).findByText('Create item')).toBeInTheDocument();
		expect(allow).toHaveTextContent('#3allow1 action');
		expect(within(allow).getByText('List items')).toBeInTheDocument();
		expect(ask.compareDocumentPosition(allow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		await user.click(within(ask).getByRole('button', { name: 'Remove the Ask actions rule' }));
		expect(onRules).toHaveBeenLastCalledWith([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', match_mode: 'prefix' },
			{ effect: 'allow', methods: null, path: null, operations: ['listItems'] },
		]);
	});

	it('warns when an earlier path rule already allows an Ask action — it would never ask', async () => {
		stubOperations();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'allow', methods: ['POST'], path: '/items', match_mode: 'prefix' },
					{
						effect: 'require-approval',
						methods: null,
						path: null,
						operations: ['createItem', 'listItems'],
					},
				]}
			/>,
		);
		const warning = await screen.findByTestId('t-ask-never-fires');
		expect(warning).toHaveAttribute('role', 'status');
		// POST /items is allowed by rule #1; GET /items still asks.
		expect(warning).toHaveTextContent(
			'one Ask action is already allowed there, so it will run without asking.',
		);
	});

	it('no never-fires warning when the path rules leave the Ask actions alone', async () => {
		stubOperations();
		renderWithProviders(
			<Host
				initial="custom"
				withApi
				initialRules={[
					{ effect: 'allow', methods: ['GET'], path: '/other', match_mode: 'prefix' },
					{
						effect: 'require-approval',
						methods: null,
						path: null,
						operations: ['createItem'],
					},
				]}
			/>,
		);
		await screen.findByText('Create item');
		expect(screen.queryByTestId('t-ask-never-fires')).toBeNull();
	});

	it('Effective access counts Ask apart from allowed', async () => {
		stubOperations();
		const user = userEvent.setup();
		renderWithProviders(<Host initial="read-ask" withApi />);
		await user.click(screen.getByRole('button', { name: /Effective access/ }));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: /Effective access/ })).toHaveTextContent(
				'1 allowed · 1 ask · 0 denied',
			),
		);
		expect(await screen.findByTestId('op-impact-totals')).toHaveTextContent(
			'1 allowed · 1 ask · 0 denied',
		);
	});

	it('the presets are the shared radio-card group — Home and End jump its ends', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host />);
		const group = screen.getByTestId('t-rules-preset');
		const radios = within(group).getAllByRole('radio');
		const last = radios.length - 1;
		radios[2].focus();

		await user.keyboard('{Home}');
		expect(radios[0]).toBeChecked();
		expect(radios[0]).toHaveFocus();
		// End lands on Custom rules (its editor then takes focus; the selection is what's pinned).
		await user.keyboard('{End}');
		expect(radios[last]).toBeChecked();
		radios[0].focus();
		await user.keyboard('{ArrowUp}');
		expect(radios[last]).toBeChecked();
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
			).toHaveTextContent('1 allowed · 0 ask · 1 denied'),
		);
		expect(within(rows[1]).getByTestId('t-tester')).toBeInTheDocument();
		expect(within(rows[1]).getByRole('button', { name: /Try a request/ })).toHaveAttribute(
			'aria-expanded',
			'true',
		);
	});

	it('says nothing about path denies it cannot judge: no operation list, no warning', () => {
		// The vague "a deny there can block these actions" hint is retired: the
		// warning names a real conflict, and with no list there is none to name.
		const deny: PermissionRule = { effect: 'deny', methods: ['DELETE'], path: '.*' };
		renderWithProviders(
			<Host initial="custom" initialRules={[deny, actionRule(['repos/delete'])]} />,
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
		await user.click(screen.getByRole('radio', { name: /^Read only$/ }));
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
