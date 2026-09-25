import { useState } from 'react';
import { checkA11y, renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { RadioCardGroup } from '@/shared/ui/RadioCardGroup';
import type { RadioCardOption } from '@/shared/ui/RadioCardGroup';

const options: RadioCardOption[] = [
	{ value: 'a', label: 'Alpha', description: 'First' },
	{ value: 'b', label: 'Bravo', disabled: true },
	{ value: 'c', label: 'Charlie' },
];

function Harness({ initial = 'a', disabled }: { initial?: string | null; disabled?: boolean }) {
	const [value, setValue] = useState<string | null>(initial);
	return (
		<RadioCardGroup
			options={options}
			value={value}
			onChange={setValue}
			ariaLabel="Pick one"
			disabled={disabled}
		/>
	);
}

describe('RadioCardGroup', () => {
	it('renders a named radiogroup with checked state and descriptions', async () => {
		const { container } = renderWithProviders(<Harness />);
		expect(screen.getByRole('radiogroup', { name: 'Pick one' })).toBeInTheDocument();
		const alpha = screen.getByRole('radio', { name: 'Alpha' });
		expect(alpha).toHaveAttribute('aria-checked', 'true');
		expect(alpha).toHaveAccessibleDescription('First');
		await checkA11y(container);
	});

	it('selects on click and ignores disabled options', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		await user.click(screen.getByRole('radio', { name: 'Charlie' }));
		expect(screen.getByRole('radio', { name: 'Charlie' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		expect(screen.getByRole('radio', { name: 'Bravo' })).toBeDisabled();
	});

	it('arrow keys move focus + selection, skipping disabled options and wrapping', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);
		const alpha = screen.getByRole('radio', { name: 'Alpha' });
		const charlie = screen.getByRole('radio', { name: 'Charlie' });
		alpha.focus();
		await user.keyboard('{ArrowDown}');
		expect(charlie).toHaveFocus();
		expect(charlie).toHaveAttribute('aria-checked', 'true');
		await user.keyboard('{ArrowRight}');
		expect(alpha).toHaveFocus();
		await user.keyboard('{ArrowUp}');
		expect(charlie).toHaveFocus();
	});

	it('uses a single tab stop (first enabled option when nothing is checked)', () => {
		renderWithProviders(<Harness initial={null} />);
		expect(screen.getByRole('radio', { name: 'Alpha' })).toHaveAttribute('tabindex', '0');
		expect(screen.getByRole('radio', { name: 'Charlie' })).toHaveAttribute('tabindex', '-1');
	});

	it('Space selects the focused option', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness initial={null} />);
		screen.getByRole('radio', { name: 'Alpha' }).focus();
		await user.keyboard(' ');
		expect(screen.getByRole('radio', { name: 'Alpha' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
	});

	it('disables every option when the group is disabled', () => {
		renderWithProviders(<Harness disabled />);
		for (const r of screen.getAllByRole('radio')) expect(r).toBeDisabled();
	});
});
