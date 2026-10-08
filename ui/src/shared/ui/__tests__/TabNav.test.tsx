import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Key, LayoutDashboard } from 'lucide-react';
import { TabNav, type TabNavChangeSource } from '@/shared/ui/TabNav';

const options = [
	{ value: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
	{ value: 'keys', label: 'Keys', icon: <Key className="h-4 w-4" />, count: 2 },
	{ value: 'settings', label: 'Settings' },
];

function Harness({ onChange }: { onChange?: (v: string, source: TabNavChangeSource) => void }) {
	const [value, setValue] = useState('overview');
	return (
		<>
			<TabNav
				options={options}
				value={value}
				onChange={(v, source) => {
					setValue(v);
					onChange?.(v, source);
				}}
				ariaLabel="Detail sections"
				getTabId={(v) => `tab-${v}`}
				getControls={(v) => `panel-${v}`}
			/>
			<div role="tabpanel" id={`panel-${value}`} aria-labelledby={`tab-${value}`}>
				{value}
			</div>
		</>
	);
}

describe('TabNav', () => {
	it('renders ARIA tab semantics with the active tab selected', () => {
		renderWithProviders(<Harness />);
		const tablist = screen.getByRole('tablist', { name: 'Detail sections' });
		expect(tablist).toBeInTheDocument();
		expect(screen.getAllByRole('tab')).toHaveLength(3);
		expect(screen.getByRole('tab', { name: /Overview/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(screen.getByRole('tab', { name: /Settings/ })).toHaveAttribute(
			'aria-selected',
			'false',
		);
	});

	it('selects a tab on click and wires aria-controls to the panel', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(<Harness onChange={onChange} />);

		await user.click(screen.getByRole('tab', { name: /Settings/ }));
		expect(onChange).toHaveBeenCalledWith('settings', 'click');
		expect(screen.getByRole('tab', { name: /Settings/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(screen.getByRole('tabpanel')).toHaveTextContent('settings');
		expect(screen.getByRole('tab', { name: /Settings/ })).toHaveAttribute(
			'aria-controls',
			'panel-settings',
		);
	});

	it('shows the count badge when provided', () => {
		renderWithProviders(<Harness />);
		expect(screen.getByRole('tab', { name: /Keys/ })).toHaveTextContent('2');
	});

	it('moves selection with arrow keys (automatic activation, wrapping)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);

		await user.click(screen.getByRole('tab', { name: /Overview/ }));
		await user.keyboard('{ArrowRight}');
		expect(screen.getByRole('tab', { name: /Keys/ })).toHaveAttribute('aria-selected', 'true');
		await user.keyboard('{End}');
		expect(screen.getByRole('tab', { name: /Settings/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		// Wraps from the last tab back to the first.
		await user.keyboard('{ArrowRight}');
		expect(screen.getByRole('tab', { name: /Overview/ })).toHaveAttribute(
			'aria-selected',
			'true',
		);
	});

	it('keeps a roving tabIndex — only the active tab is focusable', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		expect(screen.getByRole('tab', { name: /Overview/ })).toHaveAttribute('tabindex', '0');
		expect(screen.getByRole('tab', { name: /Keys/ })).toHaveAttribute('tabindex', '-1');

		await user.click(screen.getByRole('tab', { name: /Keys/ }));
		expect(screen.getByRole('tab', { name: /Keys/ })).toHaveAttribute('tabindex', '0');
		expect(screen.getByRole('tab', { name: /Overview/ })).toHaveAttribute('tabindex', '-1');
	});

	it('fill: the tabs split the bar in equal shares', () => {
		renderWithProviders(
			<div style={{ width: 600 }}>
				<TabNav
					options={[
						{ value: 'a', label: 'A much longer first label' },
						{ value: 'b', label: 'Short' },
					]}
					value="a"
					onChange={() => {}}
					ariaLabel="Routes"
					fill
				/>
			</div>,
		);
		const [first, second] = screen.getAllByRole('tab');
		expect(first).toHaveClass('flex-1', 'basis-0', 'justify-center');
		expect(second).toHaveClass('flex-1', 'basis-0', 'justify-center');
		const a = first.getBoundingClientRect().width;
		const b = second.getBoundingClientRect().width;
		expect(a).toBeGreaterThan(0);
		expect(Math.abs(a - b)).toBeLessThan(1);
		expect(a + b).toBeCloseTo(600, 0);
	});

	it('without fill, each tab is as wide as its label', () => {
		renderWithProviders(<Harness />);
		for (const tab of screen.getAllByRole('tab')) {
			expect(tab).toHaveClass('shrink-0');
			expect(tab).not.toHaveClass('flex-1');
		}
	});

	it('tells the host how a tab was chosen: a click, or an arrow key', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(<Harness onChange={onChange} />);

		await user.click(screen.getByRole('tab', { name: /Keys/ }));
		expect(onChange).toHaveBeenLastCalledWith('keys', 'click');
		await user.keyboard('{ArrowRight}');
		expect(onChange).toHaveBeenLastCalledWith('settings', 'arrow');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Harness />);
		await checkA11y(container);
	});
});
