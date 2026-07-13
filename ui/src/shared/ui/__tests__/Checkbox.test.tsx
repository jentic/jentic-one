import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Checkbox } from '@/shared/ui/Checkbox';

describe('Checkbox', () => {
	it('toggles when clicked', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [checked, setChecked] = useState(false);
			return (
				<Checkbox checked={checked} onChange={setChecked}>
					Accept terms
				</Checkbox>
			);
		}
		renderWithProviders(<Harness />);
		const box = screen.getByRole('checkbox', { name: 'Accept terms' });
		expect(box).toHaveAttribute('aria-checked', 'false');
		await user.click(box);
		expect(box).toHaveAttribute('aria-checked', 'true');
	});

	it('does not toggle when disabled', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(
			<Checkbox checked={false} onChange={onChange} disabled ariaLabel="Locked" />,
		);
		await user.click(screen.getByRole('checkbox', { name: 'Locked' }));
		expect(onChange).not.toHaveBeenCalled();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<Checkbox checked={false} onChange={() => {}}>
				Subscribe
			</Checkbox>,
		);
		await checkA11y(container);
	});
});
