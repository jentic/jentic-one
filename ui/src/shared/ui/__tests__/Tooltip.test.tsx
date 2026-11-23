import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { Tooltip, quietHoverUntilPointerMoves } from '@/shared/ui/Tooltip';
import { fireEvent } from '@testing-library/react';

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

	it('holds a hover open after quietHoverUntilPointerMoves until the pointer moves', async () => {
		renderWithProviders(
			<Tooltip content="the full value" delayMs={0}>
				<span>trigger</span>
			</Tooltip>,
		);
		quietHoverUntilPointerMoves();
		// The trigger slid under a resting pointer: an enter with no movement.
		const trigger = screen.getByText('trigger');
		fireEvent.mouseEnter(trigger);
		expect(screen.queryByRole('tooltip')).toBeNull();
		// The pointer moves over it: the held hover opens, with no leave and
		// re-enter needed.
		fireEvent.pointerMove(trigger);
		expect(await screen.findByRole('tooltip')).toHaveTextContent('the full value');
		fireEvent.mouseLeave(trigger);
		await waitFor(() => expect(screen.queryByRole('tooltip')).toBeNull());
		// Unheld, a plain enter opens as before.
		fireEvent.mouseEnter(trigger);
		expect(await screen.findByRole('tooltip')).toBeInTheDocument();
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

	it('keeps a stable aria-describedby target even while closed', () => {
		const { container } = renderWithProviders(
			<Tooltip content="described text">
				<span>trigger</span>
			</Tooltip>,
		);
		// No visible tooltip bubble yet…
		expect(screen.queryByRole('tooltip')).toBeNull();
		// …but the wrapper's aria-describedby resolves to a rendered node that
		// carries the description, so SR announces it at focus time.
		const wrapper = container.querySelector('[aria-describedby]');
		expect(wrapper).not.toBeNull();
		const describedId = wrapper?.getAttribute('aria-describedby');
		expect(describedId).toBeTruthy();
		const descNode = describedId ? document.getElementById(describedId) : null;
		expect(descNode).not.toBeNull();
		expect(descNode).toHaveTextContent('described text');
	});

	it('puts aria-describedby on the focusable child (not the wrapper) when interactive', () => {
		const { container } = renderWithProviders(
			<Tooltip interactiveChild content="tip">
				<button type="button">real control</button>
			</Tooltip>,
		);
		// `aria-describedby` is NOT inherited from the wrapper, so it must live on
		// the button that actually receives focus.
		const button = screen.getByRole('button', { name: 'real control' });
		const describedId = button.getAttribute('aria-describedby');
		expect(describedId).toBeTruthy();
		const descNode = describedId ? document.getElementById(describedId) : null;
		expect(descNode).not.toBeNull();
		expect(descNode).toHaveTextContent('tip');

		// The wrapper carries neither the description nor a tab stop, so the
		// control isn't announced silently and isn't a double tab stop.
		const wrapper = button.parentElement as HTMLElement;
		expect(wrapper.tagName).toBe('SPAN');
		expect(wrapper.hasAttribute('aria-describedby')).toBe(false);
		expect(wrapper.hasAttribute('tabindex')).toBe(false);

		// Exactly one tab stop: the button. Nothing else in the subtree is
		// focusable via tabindex.
		expect(container.querySelectorAll('[tabindex]')).toHaveLength(0);
	});

	it('keeps the wrapper focusable when the child is not interactive (default)', () => {
		const { container } = renderWithProviders(
			<Tooltip content="tip">
				<span>plain text</span>
			</Tooltip>,
		);
		const wrapper = container.querySelector('span[aria-describedby]');
		expect(wrapper?.getAttribute('tabindex')).toBe('0');
	});

	it('dismisses on Escape without moving focus or pointer (WCAG 1.4.13)', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Tooltip content="escapable">
				<span>trigger</span>
			</Tooltip>,
		);
		// Keyboard-opened: focus stays on the trigger, Escape closes.
		await user.tab();
		await screen.findByRole('tooltip');
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('tooltip')).toBeNull();

		// Hover-opened (focus elsewhere): document-level Escape still closes.
		await user.hover(screen.getByText('trigger'));
		await screen.findByRole('tooltip');
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('tooltip')).toBeNull();
	});

	it('appends to (not clobbers) an existing aria-describedby on an interactive child', () => {
		renderWithProviders(
			<>
				<span id="prior-desc">prior description</span>
				<Tooltip interactiveChild content="tip">
					<button type="button" aria-describedby="prior-desc">
						real control
					</button>
				</Tooltip>
			</>,
		);
		const button = screen.getByRole('button', { name: 'real control' });
		const ids = (button.getAttribute('aria-describedby') ?? '').split(/\s+/);
		expect(ids).toContain('prior-desc');
		expect(ids).toHaveLength(2);
		const tipNode = document.getElementById(ids.find((id) => id !== 'prior-desc') ?? '');
		expect(tipNode).toHaveTextContent('tip');
	});
});
