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

	it('is a tonal field with a faint resting edge; an error turns the edge red', () => {
		renderWithProviders(
			<div>
				<Input aria-label="Plain" />
				<Input aria-label="Bad" error="Required" />
			</div>,
		);
		const plain = screen.getByLabelText('Plain');
		expect(plain).toHaveClass('bg-field', 'rounded-field', 'border', 'border-control-edge');
		// The resting edge is visible (not transparent) so an empty field reads.
		expect(getComputedStyle(plain).borderTopColor).not.toBe('rgba(0, 0, 0, 0)');
		const bad = screen.getByLabelText('Bad');
		expect(bad).toHaveClass('border-danger');
		expect(bad).not.toHaveClass('border-control-edge');
	});

	it('steps one tone lighter inside a card (context-aware fill)', () => {
		renderWithProviders(
			<div>
				<Input aria-label="Page field" />
				<div className="[--field-bg:var(--surface-field)]">
					<Input aria-label="Card field" />
				</div>
			</div>,
		);
		const page = getComputedStyle(screen.getByLabelText('Page field')).backgroundColor;
		const card = getComputedStyle(screen.getByLabelText('Card field')).backgroundColor;
		expect(page).not.toBe(card);
	});

	it('draws a visible inset ring on focus', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Input aria-label="Focus me" />);
		const input = screen.getByLabelText('Focus me');
		await user.click(input);
		expect(getComputedStyle(input).boxShadow).not.toBe('none');
	});
});
