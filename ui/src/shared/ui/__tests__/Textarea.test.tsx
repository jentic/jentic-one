import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Textarea } from '@/shared/ui/Textarea';

describe('Textarea', () => {
	it('accepts typed text', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Textarea aria-label="Notes" />);
		const el = screen.getByLabelText('Notes');
		await user.type(el, 'multi\nline');
		expect(el).toHaveValue('multi\nline');
	});

	it('renders an error message', () => {
		renderWithProviders(<Textarea aria-label="Notes" error="Too short" />);
		expect(screen.getByRole('alert')).toHaveTextContent('Too short');
	});

	it('has no critical a11y violations', async () => {
		function Harness() {
			const [value, setValue] = useState('');
			return (
				<div>
					<label htmlFor="a11y-ta">Bio</label>
					<Textarea
						id="a11y-ta"
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
