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
