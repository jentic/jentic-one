import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { KillSwitch } from '@/shared/ui/KillSwitch';

const BASE_PROPS = {
	suspendAriaLabel: 'Suspend toolkit (kill switch)',
	restoreAriaLabel: 'Restore toolkit access',
	suspendPrompt: 'Block keys + agents?',
	restorePrompt: 'Restore access?',
};

describe('KillSwitch', () => {
	it('arms an inline confirm and only toggles after the confirm click', async () => {
		const user = userEvent.setup();
		const onToggle = vi.fn();
		renderWithProviders(<KillSwitch active onToggle={onToggle} {...BASE_PROPS} />);

		const pill = screen.getByRole('button', { name: 'Suspend toolkit (kill switch)' });
		expect(pill).toHaveTextContent('Active');
		expect(pill).toHaveAttribute('aria-pressed', 'true');

		await user.click(pill);
		expect(onToggle).not.toHaveBeenCalled();
		expect(screen.getByText('Block keys + agents?')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: /Kill/ }));
		expect(onToggle).toHaveBeenCalledExactlyOnceWith(false);
		// Confirm group closes after applying.
		expect(screen.queryByText('Block keys + agents?')).not.toBeInTheDocument();
	});

	it('offers the restore flow when suspended', async () => {
		const user = userEvent.setup();
		const onToggle = vi.fn();
		renderWithProviders(
			<KillSwitch
				active={false}
				onToggle={onToggle}
				inactiveLabel="Disabled"
				{...BASE_PROPS}
			/>,
		);

		const pill = screen.getByRole('button', { name: 'Restore toolkit access' });
		expect(pill).toHaveTextContent('Disabled');
		expect(pill).toHaveAttribute('aria-pressed', 'false');

		await user.click(pill);
		expect(screen.getByText('Restore access?')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Restore' }));
		expect(onToggle).toHaveBeenCalledExactlyOnceWith(true);
	});

	it('cancels without toggling', async () => {
		const user = userEvent.setup();
		const onToggle = vi.fn();
		renderWithProviders(<KillSwitch active onToggle={onToggle} {...BASE_PROPS} />);

		await user.click(screen.getByRole('button', { name: 'Suspend toolkit (kill switch)' }));
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(onToggle).not.toHaveBeenCalled();
		expect(screen.queryByText('Block keys + agents?')).not.toBeInTheDocument();
	});

	it('disables the pill while the mutation is pending', () => {
		renderWithProviders(<KillSwitch active pending onToggle={vi.fn()} {...BASE_PROPS} />);
		expect(
			screen.getByRole('button', { name: 'Suspend toolkit (kill switch)' }),
		).toBeDisabled();
	});

	it('is accessible with the confirm group open', async () => {
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<KillSwitch active onToggle={vi.fn()} {...BASE_PROPS} />,
		);
		await user.click(screen.getByRole('button', { name: 'Suspend toolkit (kill switch)' }));
		expect(screen.getByRole('group', { name: 'Block keys + agents?' })).toBeInTheDocument();
		await checkA11y(container);
	});
});
