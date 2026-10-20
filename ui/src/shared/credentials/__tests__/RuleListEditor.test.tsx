import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { examplePath, examplePathPrefix } from '@/shared/credentials/lib/path-completion';

/** Real local state, so commits mutate the list as the hosts do. */
function Harness({
	initial = [],
	pathSuggestions,
}: {
	initial?: PermissionRule[];
	pathSuggestions?: string[];
}) {
	const [rules, setRules] = useState<PermissionRule[]>(initial);
	return (
		<>
			<RuleListEditor rules={rules} onChange={setRules} pathSuggestions={pathSuggestions} />
			<output data-testid="state">{JSON.stringify(rules)}</output>
		</>
	);
}

const state = (): PermissionRule[] =>
	JSON.parse(screen.getByTestId('state').textContent ?? '[]') as PermissionRule[];

describe('RuleListEditor — focus', () => {
	it('focuses the form\'s first field after "Add rule", and returns focus to it on Cancel', async () => {
		const user = userEvent.setup();
		render(<Harness />);
		await user.click(screen.getByRole('button', { name: 'Add rule' }));
		await waitFor(() => expect(screen.getByRole('button', { name: /allow/i })).toHaveFocus());

		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Add rule' })).toHaveFocus());
	});

	it("returns focus to the row's Edit button when its edit closes", async () => {
		const user = userEvent.setup();
		render(<Harness initial={[{ effect: 'allow', methods: ['GET'], path: null }]} />);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		await waitFor(() => expect(screen.getByRole('button', { name: /allow/i })).toHaveFocus());
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Edit rule' })).toHaveFocus(),
		);
	});
});

describe('RuleListEditor — an open edit is addressed by position', () => {
	it("locks every other row's Delete / Move / Edit (and Add rule) while a form is open", async () => {
		const user = userEvent.setup();
		render(
			<Harness
				initial={[
					{ effect: 'allow', methods: ['GET'], path: null },
					{ effect: 'deny', methods: ['DELETE'], path: null },
				]}
			/>,
		);
		await user.click(screen.getAllByRole('button', { name: 'Edit rule' })[1]);
		expect(screen.getByRole('button', { name: 'Delete rule' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Move rule down' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Edit rule' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Add rule' })).toBeDisabled();

		// Saving lands on the rule that was opened, then the verbs come back.
		await user.click(screen.getByRole('button', { name: 'POST' }));
		await user.click(screen.getByRole('button', { name: 'Save' }));
		expect(state()[1]?.methods).toEqual(['DELETE', 'POST']);
		expect(state()[0]?.methods).toEqual(['GET']);
		expect(screen.getAllByRole('button', { name: 'Delete rule' })[0]).toBeEnabled();
	});
});

describe('RuleListEditor — match mode of a stored rule', () => {
	it('reads a path with no mode as regex (the backend default), so ".*" survives an edit', async () => {
		const user = userEvent.setup();
		render(<Harness initial={[{ effect: 'allow', path: '.*' }]} />);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('regex');
		await user.click(screen.getByRole('button', { name: 'GET' }));
		await user.click(screen.getByRole('button', { name: 'Save' }));
		expect(state()[0]).toMatchObject({ path: '.*', match_mode: 'regex', methods: ['GET'] });
	});

	it('keeps prefix as the default for a brand-new rule', async () => {
		const user = userEvent.setup();
		render(<Harness />);
		await user.click(screen.getByRole('button', { name: 'Add rule' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('prefix');
	});
});

describe('RuleListEditor — an op-template path under regex', () => {
	// Narrowing a broad preset is the route in: the stored rule is regex (the
	// backend default), so the draft opens in regex and the path the operator
	// then types comes from the op templates the field suggests.
	const regexTemplateRule: PermissionRule = {
		effect: 'allow',
		methods: ['GET'],
		path: '/repos/{owner}/{repo}',
		match_mode: 'regex',
	};

	it('marks the saved row as never matching, and spells out the mode', () => {
		render(<Harness initial={[regexTemplateRule]} />);
		expect(screen.getByText('/repos/{owner}/{repo} (regex)')).toBeInTheDocument();
		expect(screen.getByRole('status', { name: /matches no real request/i })).toBeVisible();
	});

	it('does not mark the same path under exact', () => {
		render(<Harness initial={[{ ...regexTemplateRule, match_mode: 'exact' }]} />);
		expect(screen.getByText('/repos/{owner}/{repo} (exact)')).toBeInTheDocument();
		expect(screen.queryByText('never matches')).not.toBeInTheDocument();
	});

	it('warns in the open draft and switches the mode on one click', async () => {
		const user = userEvent.setup();
		render(<Harness initial={[regexTemplateRule]} />);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('regex');
		expect(screen.getByTestId('rule-placeholder-in-regex')).toBeVisible();

		await user.click(screen.getByRole('button', { name: 'Use prefix' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('prefix');
		expect(screen.queryByTestId('rule-placeholder-in-regex')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Save' }));
		expect(state()[0]).toMatchObject({
			path: '/repos/{owner}/{repo}',
			match_mode: 'prefix',
		});
	});

	it('takes a regex draft to exact when a template path is picked from the suggestions', async () => {
		const user = userEvent.setup();
		render(
			<Harness
				initial={[{ effect: 'allow', methods: ['GET'], path: '.*' }]}
				pathSuggestions={['/repos/{owner}/{repo}']}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('regex');

		await user.clear(screen.getByLabelText('Path pattern'));
		await user.click(screen.getByRole('option', { name: '/repos/{owner}/{repo}' }));

		expect(screen.getByLabelText('Path match mode')).toHaveValue('exact');
		await user.click(screen.getByRole('button', { name: 'Save' }));
		expect(state()[0]).toMatchObject({
			path: '/repos/{owner}/{repo}',
			match_mode: 'exact',
		});
	});

	it('leaves a deliberate regex draft on regex when the picked path has no placeholder', async () => {
		const user = userEvent.setup();
		render(
			<Harness
				initial={[{ effect: 'allow', methods: ['GET'], path: '.*' }]}
				pathSuggestions={['/v1/charges']}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		await user.clear(screen.getByLabelText('Path pattern'));
		await user.click(screen.getByRole('option', { name: '/v1/charges' }));
		expect(screen.getByLabelText('Path match mode')).toHaveValue('regex');
	});
});

describe('RuleListEditor — the path placeholder', () => {
	it("names a route of the API being edited, not another vendor's", async () => {
		const user = userEvent.setup();
		render(<Harness pathSuggestions={['/v1/charges/{id}', '/v1/charges', '/v1/customers']} />);
		await user.click(screen.getByRole('button', { name: 'Add rule' }));
		const path = screen.getByLabelText('Path pattern');
		expect(path).toHaveAttribute('placeholder', '/v1');
		expect(path.getAttribute('placeholder')).not.toContain('repos');
	});

	it('falls back to a generic example when the API has no known operations', async () => {
		const user = userEvent.setup();
		render(<Harness />);
		await user.click(screen.getByRole('button', { name: 'Add rule' }));
		expect(screen.getByLabelText('Path pattern')).toHaveAttribute('placeholder', '/resource');
	});
});

describe('examplePath / examplePathPrefix', () => {
	it('prefer the shortest real path and its first segment', () => {
		expect(examplePath(['/v1/charges/{id}', '/v1/charges'])).toBe('/v1/charges');
		expect(examplePathPrefix(['/repos/{owner}/{repo}'])).toBe('/repos');
		expect(examplePath(['/'])).toBe('/resource');
		expect(examplePath(undefined)).toBe('/resource');
	});
});

describe('RuleListEditor — operation ids read as actions', () => {
	const catalog = {
		status: 'ready' as const,
		operations: [
			{
				operation_id: 'op_cols',
				method: 'GET',
				path: '/v1/boardColumns',
				name: 'List columns',
			},
			{ operation_id: 'op_tags', method: 'GET', path: '/v1/tags', name: 'List tags' },
		],
	};

	it.each([
		['allow', 'allow'],
		['require-approval', 'ask'],
		['deny', 'deny'],
	] as const)(
		'a %s action rule reads "%s · N actions", each action by method and path',
		(effect, word) => {
			render(
				<RuleListEditor
					rules={[
						{ effect, methods: null, path: null, operations: ['op_tags', 'op_cols'] },
					]}
					onChange={() => undefined}
					actionCatalog={catalog}
				/>,
			);
			const rules = screen.getByRole('list', { name: 'Rules, in evaluation order' });
			expect(rules).toHaveTextContent(`#1${word}2 actions`);
			// Not the "any method /" a path rule with no conditions would read as.
			expect(rules).not.toHaveTextContent('any method');
			const actions = screen.getByRole('list', { name: 'Actions' });
			expect(actions).toHaveTextContent('GET/v1/boardColumnsList columns');
			expect(actions).toHaveTextContent('GET/v1/tagsList tags');
			expect(rules).not.toHaveTextContent('op_tags');
		},
	);

	it('a rule narrowed by method and path keeps them, its actions listed beneath', () => {
		render(
			<RuleListEditor
				rules={[
					{
						effect: 'allow',
						methods: ['GET'],
						path: '/v1/boardColumns',
						operations: ['op_cols'],
					},
				]}
				onChange={() => undefined}
				actionCatalog={catalog}
			/>,
		);
		const rules = screen.getByRole('list', { name: 'Rules, in evaluation order' });
		expect(rules).toHaveTextContent('GET/v1/boardColumns');
		expect(screen.getByRole('list', { name: 'Actions' })).toHaveTextContent('List columns');
		expect(rules).not.toHaveTextContent('op_cols');
	});
});

describe('RuleListEditor — "no ops affected" judges every condition together', () => {
	const catalog = {
		status: 'ready' as const,
		operations: [
			{
				operation_id: 'op_post',
				method: 'POST',
				path: '/v1/boardColumns',
				name: 'Add a column',
			},
			{ operation_id: 'op_put', method: 'PUT', path: '/v1/boardColumns/{id}', name: 'Edit' },
		],
	};
	const row = (rule: PermissionRule) => {
		render(
			<RuleListEditor rules={[rule]} onChange={() => undefined} actionCatalog={catalog} />,
		);
		return screen.queryByLabelText('No operations affected');
	};

	it('flags a GET on a path the API only has POST and PUT for', () => {
		expect(
			row({
				effect: 'allow',
				methods: ['GET'],
				path: '/v1/boardColumns',
				match_mode: 'prefix',
			}),
		).toBeInTheDocument();
	});

	it('flags a method + path added to an action rule that excludes its own actions', () => {
		expect(
			row({
				effect: 'allow',
				methods: ['GET'],
				path: '/v1/boardColumns',
				match_mode: 'prefix',
				operations: ['op_post'],
			}),
		).toBeInTheDocument();
	});

	it('stays quiet for a rule that reaches an operation', () => {
		expect(
			row({
				effect: 'allow',
				methods: ['POST'],
				path: '/v1/boardColumns',
				match_mode: 'prefix',
			}),
		).toBeNull();
	});
});
