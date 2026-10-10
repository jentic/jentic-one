/**
 * AccessRulesStep — the shared "What can this agent call?" step: presets as one
 * labelled radio group, the coverage caveat, the rules editor under Custom, and
 * the optional inline dry run of the rules as they stand (unsaved).
 */
import { useState } from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';
import { AccessRulesStep } from '@/shared/credentials/components/AccessRulesStep';
import type { RulesPreset, ScopeReach } from '@/shared/credentials/lib/accessPresets';

function Host({
	reach = 'pinned',
	initial = null,
	tester = false,
}: {
	reach?: ScopeReach;
	initial?: RulesPreset | null;
	tester?: boolean;
}) {
	const [preset, setPreset] = useState<RulesPreset | null>(initial);
	const [rules, setRules] = useState<PermissionRule[]>([]);
	return (
		<AccessRulesStep
			reach={reach}
			preset={preset}
			onPresetChange={setPreset}
			customRules={rules}
			onCustomRulesChange={setRules}
			tester={tester}
			testIdPrefix="t"
		/>
	);
}

describe('AccessRulesStep', () => {
	beforeEach(async () => {
		await page.viewport(900, 900);
		setToken('test-token');
	});

	it('is one labelled radio group with nothing preselected unless the host says so', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host />);
		const group = screen.getByRole('radiogroup', { name: 'What can this agent call?' });
		const radios = within(group).getAllByRole('radio');
		for (const radio of radios) expect(radio).not.toBeChecked();
		expect(radios).toHaveLength(3);
		expect(screen.queryByRole('button', { name: 'Add rule' })).toBeNull();

		await user.click(within(group).getByRole('radio', { name: /Custom rules/ }));
		expect(screen.getByRole('button', { name: 'Add rule' })).toBeVisible();
		// No tester unless asked for — the workspace bind has none.
		expect(screen.queryByTestId('t-tester')).toBeNull();
	});

	it('says what Allow all reaches and notes coverage past a pinned API', () => {
		renderWithProviders(<Host reach="vendor-wide" />);
		expect(
			screen.getByRole('radio', { name: /Allow all operations/ }),
		).toHaveAccessibleDescription(/every API and version this credential covers/);
		expect(screen.getByTestId('t-coverage-note')).toHaveTextContent('every API of its vendor');
	});

	it('the inline tester follows the preset without being asked again', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host initial="custom" tester />);
		const tester = screen.getByTestId('t-tester');
		expect(within(tester).getByRole('heading', { name: 'Try a request' })).toBeInTheDocument();
		await user.selectOptions(within(tester).getByLabelText('HTTP method'), 'POST');
		await user.type(within(tester).getByLabelText('Request path'), '/items');
		await user.click(within(tester).getByRole('button', { name: 'Test' }));
		expect(await within(tester).findByTestId('rule-verdict')).toHaveTextContent(
			'Denied POST /items — no rule matched (default deny)',
		);

		await user.click(screen.getByRole('radio', { name: /Allow all operations/ }));
		await waitFor(() =>
			expect(within(tester).getByTestId('rule-verdict')).toHaveTextContent(
				'Allowed POST /items — allowed by “Allow all operations”',
			),
		);
		await user.click(screen.getByRole('radio', { name: /Read-only/ }));
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
