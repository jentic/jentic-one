import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { ScopeGroup } from '@/shared/ui/ScopeGroup';
import type { ScopeGroup as ScopeGroupType, EnhancedScope } from '@/shared/lib/scopes';

function scope(name: string, isRecommended = false): EnhancedScope {
	return { scope: name, description: `${name} description`, origin: 'platform', isRecommended };
}

const group: ScopeGroupType = {
	id: 'agents',
	name: 'Agents',
	scopes: [scope('agents:read'), scope('agents:write')],
	totalCount: 2,
};

function renderGroup(props: Partial<React.ComponentProps<typeof ScopeGroup>> = {}) {
	return renderWithProviders(
		<ScopeGroup
			group={group}
			selectedScopes={new Set<string>()}
			onToggleScope={() => {}}
			onSelectAll={() => {}}
			onDeselectAll={() => {}}
			{...props}
		/>,
	);
}

describe('ScopeGroup', () => {
	it('expands to reveal scope rows and toggles an individual scope', async () => {
		const user = userEvent.setup();
		const onToggleScope = vi.fn();
		renderGroup({ onToggleScope });

		// Collapsed by default: rows are not rendered.
		expect(screen.queryByRole('checkbox', { name: 'agents:read' })).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Agents scopes, 0 of 2 selected/ }));
		await user.click(await screen.findByRole('checkbox', { name: 'agents:read' }));
		expect(onToggleScope).toHaveBeenCalledWith('agents:read');
	});

	it('renders the per-group select-all as a sibling control (not nested)', async () => {
		const user = userEvent.setup();
		const onSelectAll = vi.fn();
		renderGroup({ onSelectAll });

		const selectAll = screen.getByRole('checkbox', { name: 'Select all Agents' });
		// The expand toggle and the select-all must not nest inside one another.
		const expandToggle = screen.getByRole('button', {
			name: /Agents scopes, 0 of 2 selected/,
		});
		expect(expandToggle.contains(selectAll)).toBe(false);

		await user.click(selectAll);
		expect(onSelectAll).toHaveBeenCalledTimes(1);
	});

	it('reflects a fully-selected group as a checked, "Deselect all" control', async () => {
		const user = userEvent.setup();
		const onDeselectAll = vi.fn();
		renderGroup({
			selectedScopes: new Set(['agents:read', 'agents:write']),
			onDeselectAll,
		});

		const selectAll = screen.getByRole('checkbox', { name: 'Deselect all Agents' });
		expect(selectAll).toHaveAttribute('aria-checked', 'true');
		await user.click(selectAll);
		expect(onDeselectAll).toHaveBeenCalledTimes(1);
	});

	it('shows a mixed (tri-state) select-all when only some scopes are selected', () => {
		renderGroup({ selectedScopes: new Set(['agents:read']) });
		expect(screen.getByRole('checkbox', { name: 'Select all Agents' })).toHaveAttribute(
			'aria-checked',
			'mixed',
		);
	});

	it('disables scopes the caller may not grant and never counts them as selectable', () => {
		// Only agents:read is grantable, so the group is "all selected" with just it.
		renderGroup({
			selectedScopes: new Set(['agents:read']),
			disabledScopes: new Set(['agents:write']),
		});
		expect(screen.getByRole('checkbox', { name: 'Deselect all Agents' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
	});

	it('has no critical a11y violations when expanded', async () => {
		const { container } = renderGroup({ defaultExpanded: true });
		await checkA11y(container);
	});
});
