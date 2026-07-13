import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { TruncateWithTooltip } from '@/shared/ui/TruncateWithTooltip';

const LONG = 'a very long string that will not fit inside a tiny fixed-width container';

/** A narrow box so the single truncated line genuinely overflows in the browser. */
function Narrow({ children }: { children: React.ReactNode }) {
	return <div style={{ width: 40 }}>{children}</div>;
}

describe('TruncateWithTooltip', () => {
	it('renders its children inline', () => {
		renderWithProviders(<TruncateWithTooltip>short</TruncateWithTooltip>);
		expect(screen.getByText('short')).toBeInTheDocument();
	});

	it('is not focusable when content does not overflow', () => {
		renderWithProviders(<TruncateWithTooltip>short</TruncateWithTooltip>);
		expect(screen.getByText('short')).not.toHaveAttribute('tabindex');
	});

	it('becomes focusable and shows a tooltip on focus when it overflows', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Narrow>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</Narrow>,
		);
		const trigger = screen.getAllByText(LONG)[0];
		expect(trigger).toHaveAttribute('tabindex', '0');

		await user.tab();
		expect(trigger).toHaveFocus();
		const tooltip = await screen.findByRole('tooltip');
		expect(tooltip).toHaveTextContent(LONG);
		// aria-describedby wires the trigger to the visible tooltip.
		expect(trigger).toHaveAttribute('aria-describedby', tooltip.id);
	});

	it('shows the tooltip on hover and hides it on leave', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Narrow>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</Narrow>,
		);
		const trigger = screen.getAllByText(LONG)[0];
		await user.hover(trigger);
		expect(await screen.findByRole('tooltip')).toBeInTheDocument();
		await user.unhover(trigger);
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
	});

	it('has no a11y violations', async () => {
		const { container } = renderWithProviders(
			<Narrow>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</Narrow>,
		);
		await checkA11y(container);
	});
});
