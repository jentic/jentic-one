import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, userEvent } from '@/__tests__/test-utils';
import { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

function Harness({ initial = [] as PermissionRule[] }: { initial?: PermissionRule[] }) {
	const [rules, setRules] = useState<PermissionRule[]>(initial);
	return (
		<>
			<RuleListEditor rules={rules} onChange={setRules} />
			<output data-testid="state">{JSON.stringify(rules)}</output>
		</>
	);
}

const state = (): PermissionRule[] => JSON.parse(screen.getByTestId('state').textContent ?? '[]');

describe('RuleListEditor (connect flow)', () => {
	it('adds a require-approval rule', async () => {
		const user = userEvent.setup();
		render(<Harness />);
		await user.click(screen.getByRole('button', { name: /add rule/i }));
		await user.click(screen.getByRole('button', { name: 'require approval' }));
		await user.click(screen.getByRole('button', { name: 'POST' }));
		await user.click(screen.getByRole('button', { name: 'Add' }));
		expect(state()).toEqual([
			expect.objectContaining({ effect: 'require-approval', methods: ['POST'] }),
		]);
	});

	it('refuses a condition-less require-approval rule', async () => {
		const user = userEvent.setup();
		render(<Harness />);
		await user.click(screen.getByRole('button', { name: /add rule/i }));
		await user.click(screen.getByRole('button', { name: 'require approval' }));
		await user.click(screen.getByRole('button', { name: 'Add' }));
		expect(screen.getByText(/"require approval" rule must constrain/)).toBeInTheDocument();
		expect(state()).toEqual([]);
	});

	it('keeps a require-approval rule as require-approval when it is edited', async () => {
		const user = userEvent.setup();
		render(
			<Harness
				initial={[
					{
						effect: 'require-approval',
						methods: ['POST'],
						path: null,
						match_mode: 'prefix',
					},
				]}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'Edit rule' }));
		await user.click(screen.getByRole('button', { name: 'Save' }));
		expect(state()[0].effect).toBe('require-approval');
	});
});
