import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Select } from '@/shared/ui/Select';

describe('Select', () => {
	it('selects an option', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [value, setValue] = useState('a');
			return (
				<div>
					<label htmlFor="sel">Pick</label>
					<Select id="sel" value={value} onChange={(e) => setValue(e.target.value)}>
						<option value="a">Alpha</option>
						<option value="b">Beta</option>
					</Select>
				</div>
			);
		}
		renderWithProviders(<Harness />);
		const select = screen.getByLabelText('Pick');
		await user.selectOptions(select, 'b');
		expect(select).toHaveValue('b');
	});

	it('renders an error message', () => {
		renderWithProviders(
			<Select aria-label="Pick" error="Required">
				<option value="">--</option>
			</Select>,
		);
		expect(screen.getByRole('alert')).toHaveTextContent('Required');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<div>
				<label htmlFor="a11y-sel">Country</label>
				<Select id="a11y-sel" defaultValue="us">
					<option value="us">United States</option>
					<option value="ie">Ireland</option>
				</Select>
			</div>,
		);
		await checkA11y(container);
	});
});
