import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { SegmentedToggle } from '@/shared/ui/SegmentedToggle';

const options = [
	{ value: 'list', label: 'List' },
	{ value: 'grid', label: 'Grid' },
];

describe('SegmentedToggle', () => {
	it('changes selection on click', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [value, setValue] = useState('list');
			return (
				<SegmentedToggle
					options={options}
					value={value}
					onChange={setValue}
					layoutId="view"
				/>
			);
		}
		renderWithProviders(<Harness />);
		await user.click(screen.getByRole('button', { name: 'Grid' }));
		// The active segment text stays in the document after the switch.
		expect(screen.getByText('Grid')).toBeInTheDocument();
	});

	it('invokes onChange with the selected value', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(
			<SegmentedToggle options={options} value="list" onChange={onChange} layoutId="view2" />,
		);
		await user.click(screen.getByRole('button', { name: 'Grid' }));
		expect(onChange).toHaveBeenCalledWith('grid');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<SegmentedToggle options={options} value="list" onChange={() => {}} layoutId="view3" />,
		);
		await checkA11y(container);
	});
});
