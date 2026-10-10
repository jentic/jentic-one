import { renderWithProviders, screen, checkA11y, waitFor } from '@/__tests__/test-utils';
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

	it('never shows a tooltip for text that fits, on hover or focus', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<div style={{ width: 400 }}>
				<TruncateWithTooltip>my-first-agent</TruncateWithTooltip>
			</div>,
		);
		const text = screen.getByText('my-first-agent');
		await user.hover(text);
		text.focus();
		expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
	});

	it('picks up overflow when its box shrinks (ResizeObserver)', async () => {
		const user = userEvent.setup();
		const { rerender } = renderWithProviders(
			<div style={{ width: 600 }}>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</div>,
		);
		const trigger = screen.getAllByText(LONG)[0]!;
		expect(trigger).not.toHaveAttribute('tabindex');
		rerender(
			<div style={{ width: 60 }}>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</div>,
		);
		await waitFor(() => expect(trigger).toHaveAttribute('tabindex', '0'));
		await user.hover(trigger);
		expect(await screen.findByRole('tooltip')).toHaveTextContent(LONG);
	});

	it('inline: sits in running text with its width budget', () => {
		renderWithProviders(
			<p style={{ width: 600 }}>
				Give{' '}
				<TruncateWithTooltip inline className="max-w-[10ch]">
					{LONG}
				</TruncateWithTooltip>{' '}
				its first API
			</p>,
		);
		const name = screen.getAllByText(LONG)[0]!;
		expect(name.tagName).toBe('SPAN');
		expect(getComputedStyle(name).display).toBe('inline-block');
		expect(name).toHaveAttribute('tabindex', '0');
	});

	it('focusable={false} stays out of the tab order but still shows on hover', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Narrow>
				<TruncateWithTooltip focusable={false}>{LONG}</TruncateWithTooltip>
			</Narrow>,
		);
		const trigger = screen.getAllByText(LONG)[0]!;
		expect(trigger).not.toHaveAttribute('tabindex');
		await user.hover(trigger);
		expect(await screen.findByRole('tooltip')).toBeInTheDocument();
	});

	it('Escape dismisses an open tooltip', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Narrow>
				<TruncateWithTooltip>{LONG}</TruncateWithTooltip>
			</Narrow>,
		);
		await user.tab();
		expect(await screen.findByRole('tooltip')).toBeInTheDocument();
		await user.keyboard('{Escape}');
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
