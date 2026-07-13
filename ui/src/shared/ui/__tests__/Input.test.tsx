import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Input } from '@/shared/ui/Input';

describe('Input', () => {
	it('accepts typed text', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Input aria-label="Field" />);
		const input = screen.getByLabelText('Field');
		await user.type(input, 'hello');
		expect(input).toHaveValue('hello');
	});

	it('renders an error message with role alert', () => {
		renderWithProviders(<Input aria-label="Field" error="Required" />);
		expect(screen.getByRole('alert')).toHaveTextContent('Required');
		expect(screen.getByLabelText('Field')).toHaveAttribute('aria-invalid', 'true');
	});

	it('toggles password visibility', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Input aria-label="Password" type="password" showPasswordToggle />);
		const input = screen.getByLabelText('Password');
		expect(input).toHaveAttribute('type', 'password');
		await user.click(screen.getByRole('button', { name: 'Show password' }));
		expect(input).toHaveAttribute('type', 'text');
	});

	it('has no critical a11y violations', async () => {
		function Harness() {
			const [value, setValue] = useState('');
			return (
				<div>
					<label htmlFor="a11y-input">Search</label>
					<Input
						id="a11y-input"
						value={value}
						onChange={(e) => setValue(e.target.value)}
					/>
				</div>
			);
		}
		const { container } = renderWithProviders(<Harness />);
		await checkA11y(container);
	});
});
