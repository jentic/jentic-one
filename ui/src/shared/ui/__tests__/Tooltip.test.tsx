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

	it('keeps a stable aria-describedby target even while closed (#9)', () => {
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

	it('puts aria-describedby on the focusable child (not the wrapper) when interactive (#2)', () => {
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
});
