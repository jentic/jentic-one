import { useState, type ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, userEvent } from '@/__tests__/test-utils';
import { PermissionRuleEditor, isEmptyAllowRule, type PermissionRuleInput } from '@/shared/ui';

type Effect = PermissionRuleInput['effect'];
const ALLOW = 'allow' as Effect;
const DENY = 'deny' as Effect;

/** Terse rule factory keeping the `effect`-enum casts out of each test case. */
function rule(over: Partial<PermissionRuleInput> & { effect: Effect }): PermissionRuleInput {
	return over;
}

/**
 * Drives the editor with real local state so clicks mutate `rules` the way the
 * real `CredentialPermissionEditor` does, and exposes the latest rules for
 * assertions.
 */
function Harness({
	initial = [] as PermissionRuleInput[],
	actionsSlot,
	beforeActions,
}: {
	initial?: PermissionRuleInput[];
	actionsSlot?: ReactNode;
	beforeActions?: ReactNode;
}) {
	const [rules, setRules] = useState<PermissionRuleInput[]>(initial);
	return (
		<>
			<PermissionRuleEditor
				rules={rules}
				onChange={setRules}
				actionsSlot={actionsSlot}
				beforeActions={beforeActions}
			/>
			<output data-testid="state">{JSON.stringify(rules)}</output>
		</>
	);
}

describe('isEmptyAllowRule', () => {
	it('flags a condition-less allow (the rule the backend rejects with 422)', () => {
		expect(isEmptyAllowRule(rule({ effect: ALLOW }))).toBe(true);
		expect(isEmptyAllowRule(rule({ effect: ALLOW, methods: [], path: '' }))).toBe(true);
		expect(isEmptyAllowRule(rule({ effect: ALLOW, path: '   ' }))).toBe(true);
	});

	it('accepts a constrained allow', () => {
		expect(isEmptyAllowRule(rule({ effect: ALLOW, path: '.*' }))).toBe(false);
		expect(isEmptyAllowRule(rule({ effect: ALLOW, methods: ['GET'] }))).toBe(false);
		expect(isEmptyAllowRule(rule({ effect: ALLOW, operations: ['op'] }))).toBe(false);
	});

	it('never flags a deny (a condition-less deny is a valid catch-all)', () => {
		expect(isEmptyAllowRule(rule({ effect: DENY }))).toBe(false);
	});
});

describe('PermissionRuleEditor', () => {
	it('"Allow all operations" emits a constrained catch-all (path ".*"), not a condition-less allow', async () => {
		const user = userEvent.setup();
		render(<Harness />);

		await user.click(screen.getByRole('button', { name: /allow all operations/i }));

		const rules = JSON.parse(screen.getByTestId('state').textContent ?? '[]');
		expect(rules).toHaveLength(1);
		expect(rules[0]).toMatchObject({ effect: 'allow', path: '.*' });
		expect(isEmptyAllowRule(rules[0])).toBe(false);
	});

	it('surfaces an inline alert when an allow rule has no constraints', () => {
		render(<Harness initial={[rule({ effect: ALLOW, methods: [], path: '' })]} />);
		expect(screen.getByRole('alert')).toHaveTextContent(/must constrain at least one/i);
	});

	it('shows no alert once the allow rule is constrained with a path', () => {
		render(<Harness initial={[rule({ effect: ALLOW, path: '.*' })]} />);
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it('offers "Allow all operations" with rules already present, appending rather than replacing', async () => {
		const user = userEvent.setup();
		render(<Harness initial={[rule({ effect: DENY, path: '/admin/.*' })]} />);

		await user.click(screen.getByRole('button', { name: /allow all operations/i }));

		// Appended, so the existing deny still decides first (first match wins).
		const rules = JSON.parse(screen.getByTestId('state').textContent ?? '[]');
		expect(rules).toHaveLength(2);
		expect(rules[0]).toMatchObject({ effect: 'deny', path: '/admin/.*' });
		expect(rules[1]).toMatchObject({ effect: 'allow', path: '.*' });
	});

	it('withholds the shortcut once the draft already grants everything', () => {
		render(<Harness initial={[rule({ effect: ALLOW, path: '.*' })]} />);
		expect(screen.queryByRole('button', { name: /allow all operations/i })).toBeNull();
	});

	it('fixes an unconstrained allow in one click from its own alert', async () => {
		const user = userEvent.setup();
		render(<Harness initial={[rule({ effect: ALLOW, methods: [], path: '' })]} />);

		await user.click(screen.getByRole('button', { name: /allow everything/i }));

		const rules = JSON.parse(screen.getByTestId('state').textContent ?? '[]');
		expect(rules[0]).toMatchObject({ path: '.*' });
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it("renders the host's verbs on the same row as its own, and the notice above it", () => {
		render(
			<Harness
				actionsSlot={<button type="button">Save rules</button>}
				beforeActions={<p>Pending changes</p>}
			/>,
		);

		const addRule = screen.getByRole('button', { name: /add rule/i });
		const save = screen.getByRole('button', { name: 'Save rules' });
		// Same row means the same flex parent.
		expect(addRule.closest('div')?.parentElement).toBe(save.closest('div')?.parentElement);
		expect(screen.getByText('Pending changes')).toBeInTheDocument();
	});
});
