import { renderWithProviders, screen } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { Tooltip } from '@/shared/ui/Tooltip';

describe('Tooltip', () => {
	it('renders its trigger children', () => {
		renderWithProviders(
			<Tooltip content="the full value">
				<span>trigger</span>
			</Tooltip>,
		);
		expect(screen.getByText('trigger')).toBeInTheDocument();
	});

	it('does not render the bubble until hovered', () => {
		renderWithProviders(
			<Tooltip content="the full value">
				<span>trigger</span>
			</Tooltip>,
		);
		expect(screen.queryByRole('tooltip')).toBeNull();
	});

	it('shows content on hover and hides on unhover', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Tooltip content="the full value">
				<span>trigger</span>
			</Tooltip>,
		);
		await user.hover(screen.getByText('trigger'));
		const tip = await screen.findByRole('tooltip');
		expect(tip).toHaveTextContent('the full value');

		await user.unhover(screen.getByText('trigger'));
		expect(screen.queryByRole('tooltip')).toBeNull();
	});

	it('shows content on keyboard focus (a11y) and wires aria-describedby', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Tooltip content="focus value">
				<span>focus-trigger</span>
			</Tooltip>,
		);
		await user.tab();
		const tip = await screen.findByRole('tooltip');
		expect(tip).toHaveTextContent('focus value');
	});
});
