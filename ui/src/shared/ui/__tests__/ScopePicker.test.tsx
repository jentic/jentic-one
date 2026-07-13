import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { ScopePicker } from '@/shared/ui/ScopePicker';
import type { EnhancedScope } from '@/shared/lib/scopes';

function scope(name: string, isRecommended = false): EnhancedScope {
	return { scope: name, description: `${name} description`, origin: 'platform', isRecommended };
}

const scopes: EnhancedScope[] = [
	scope('agents:read'),
	scope('agents:write'),
	scope('credentials:read', true),
];

/**
 * Stateful harness so the picker behaves like it does in a real editor:
 * selection lives in the parent and the callbacks mutate it.
 */
function Harness({
	initial = [],
	disabledScopes,
	showRecommended,
}: {
	initial?: string[];
	disabledScopes?: string[];
	showRecommended?: boolean;
}) {
	const [selected, setSelected] = useState<string[]>(initial);
	const disabled = new Set(disabledScopes ?? []);
	const selectable = scopes.filter((s) => !disabled.has(s.scope)).map((s) => s.scope);
	return (
		<ScopePicker
			scopes={scopes}
			selectedScopes={selected}
			disabledScopes={disabledScopes}
			showRecommended={showRecommended}
			onScopeToggle={(s) =>
				setSelected((prev) =>
					prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
				)
			}
			onSelectAll={() => setSelected(selectable)}
			onDeselectAll={() => setSelected([])}
		/>
	);
}

describe('ScopePicker', () => {
	it('counts selected against the selectable total', () => {
		renderWithProviders(<Harness initial={['agents:read']} />);
		expect(screen.getByText('1 of 3 selected')).toBeInTheDocument();
	});

	it('excludes disabled scopes from the selectable total', () => {
		// credentials:read is non-grantable → 2 selectable, agents:read selected.
		renderWithProviders(
			<Harness initial={['agents:read']} disabledScopes={['credentials:read']} />,
		);
		expect(screen.getByText('1 of 2 selected')).toBeInTheDocument();
	});

	it('flips the global toggle to "Deselect all" only once every selectable is chosen', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);

		const toggle = screen.getByRole('button', { name: 'Select all' });
		await user.click(toggle);
		expect(screen.getByRole('button', { name: 'Deselect all' })).toBeInTheDocument();
		expect(screen.getByText('3 of 3 selected')).toBeInTheDocument();
	});

	it('filters scopes by the search query', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);

		await user.type(screen.getByLabelText('Search scopes'), 'credentials');
		// The Credentials group expands on search; Agents group is filtered out.
		expect(await screen.findByRole('checkbox', { name: 'credentials:read' })).toBeVisible();
		expect(screen.queryByRole('checkbox', { name: 'agents:read' })).not.toBeInTheDocument();
	});

	it('shows an empty hint when the query matches nothing', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		await user.type(screen.getByLabelText('Search scopes'), 'nope');
		expect(await screen.findByText(/No scopes match/)).toBeInTheDocument();
	});

	it('hides "Recommended" badges when showRecommended is false', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness showRecommended={false} />);
		await user.type(screen.getByLabelText('Search scopes'), 'credentials');
		await screen.findByRole('checkbox', { name: 'credentials:read' });
		expect(screen.queryByText('Recommended')).not.toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Harness />);
		await checkA11y(container);
	});
});
