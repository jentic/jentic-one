import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, userEvent } from '@/__tests__/test-utils';
import {
	PermissionRuleEditor,
	isEmptyAllowRule,
} from '@/modules/toolkits/components/PermissionRuleEditor';
import type { PermissionRuleInput } from '@/modules/toolkits/api/types';

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
function Harness({ initial = [] as PermissionRuleInput[] }) {
	const [rules, setRules] = useState<PermissionRuleInput[]>(initial);
	return (
		<>
			<PermissionRuleEditor rules={rules} onChange={setRules} />
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
});
