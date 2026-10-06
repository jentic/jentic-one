import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { ExpandableText } from '@/shared/ui/ExpandableText';

/**
 * Free text clamped by default, expanded on request. Browser mode gives real
 * layout, so every assertion here measures the rendered box rather than
 * asserting a class and hoping it means what it says.
 */
const LONG =
	'This agent handles support tickets end to end. '.repeat(8) +
	'And it keeps going well past any sensible width.';

function lineHeightOf(el: HTMLElement): number {
	return parseFloat(getComputedStyle(el).lineHeight);
}

describe('ExpandableText', () => {
	it('offers no toggle for text that already fits', async () => {
		const { container } = renderWithProviders(
			<div style={{ width: 400 }}>
				<ExpandableText lines={2}>Short enough.</ExpandableText>
			</div>,
		);

		// A toggle on text that is whole would be an affordance that changes
		// nothing, so it is absent — the verdict comes from live layout.
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument(),
		);
		await checkA11y(container);
	});

	it('clamps to its line budget and expands on request', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<div style={{ width: 300 }}>
				<ExpandableText lines={2}>{LONG}</ExpandableText>
			</div>,
		);

		const text = screen.getByText(LONG);
		const lh = lineHeightOf(text);
		expect(text.getBoundingClientRect().height).toBeLessThan(lh * 3);

		const more = await screen.findByRole('button', { name: 'Show more' });
		expect(more).toHaveAttribute('aria-expanded', 'false');
		// The button owns the text it governs, so a screen reader announcing the
		// toggle can reach what expanding reveals.
		expect(more.getAttribute('aria-controls')).toBe(text.id);

		await user.click(more);
		await waitFor(() => expect(text.getBoundingClientRect().height).toBeGreaterThan(lh * 3));
		expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute(
			'aria-expanded',
			'true',
		);
	});

	it('keeps the toggle while expanded and collapses back', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<div style={{ width: 300 }}>
				<ExpandableText lines={1}>{LONG}</ExpandableText>
			</div>,
		);

		const text = screen.getByText(LONG);
		const lh = lineHeightOf(text);
		await user.click(await screen.findByRole('button', { name: 'Show more' }));
		await waitFor(() => expect(text.getBoundingClientRect().height).toBeGreaterThan(lh * 2));

		// Expanded text overflows nothing, so a naive re-measure would retract
		// the only way back. `Show less` stays.
		await user.click(screen.getByRole('button', { name: 'Show less' }));
		await waitFor(() => expect(text.getBoundingClientRect().height).toBeLessThan(lh * 2));
		expect(screen.getByRole('button', { name: 'Show more' })).toBeInTheDocument();
	});
});
