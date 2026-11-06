/**
 * DetailsLine — one muted summary line; "Details" reveals the rest, in place.
 */
import { describe, it, expect } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { DetailsLine } from '@/shared/ui';

describe('DetailsLine', () => {
	it('keeps the details folded until asked, then reveals them', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<DetailsLine summary="Added via GitHub PAT already">More about it</DetailsLine>,
		);
		expect(screen.getByText('Added via GitHub PAT already')).toBeVisible();
		const toggle = screen.getByRole('button', { name: /Details/ });
		expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-expanded', 'true');
		await waitFor(() => expect(screen.getByText('More about it')).toBeVisible());
	});

	it('is a plain line with nothing behind it', () => {
		renderWithProviders(<DetailsLine summary="Applies to future versions too" />);
		expect(screen.queryByRole('button', { name: /Details/ })).toBeNull();
	});

	it('points aria-controls at the panel it reveals, and only while it is there', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DetailsLine summary="Scoped to this version">Why</DetailsLine>);
		const toggle = screen.getByRole('button', { name: /Details/ });
		// Folded, the reveal is unmounted: naming its id would be a dangling `aria-controls`.
		expect(toggle).not.toHaveAttribute('aria-controls');

		await user.click(toggle);
		await waitFor(() => expect(toggle).toHaveAttribute('aria-controls'));
		const panelId = toggle.getAttribute('aria-controls')!;
		const panel = document.getElementById(panelId);
		expect(panel).not.toBeNull();
		expect(panel).toContainElement(screen.getByText('Why'));

		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await waitFor(() => expect(toggle).not.toHaveAttribute('aria-controls'));
	});

	it('opens from the keyboard', async () => {
		const user = userEvent.setup();
		renderWithProviders(<DetailsLine summary="Reused from an earlier step">How</DetailsLine>);
		const toggle = screen.getByRole('button', { name: /Details/ });
		toggle.focus();
		await user.keyboard('{Enter}');
		expect(toggle).toHaveAttribute('aria-expanded', 'true');
		await waitFor(() => expect(screen.getByText('How')).toBeVisible());

		await user.keyboard(' ');
		expect(toggle).toHaveAttribute('aria-expanded', 'false');
		expect(toggle).toHaveFocus();
	});

	it('renders no reveal at all when there is nothing behind the line', () => {
		const { container } = renderWithProviders(
			<DetailsLine summary="Applies to future versions too" data-testid="line" />,
		);
		const root = screen.getByTestId('line');
		expect(root.children).toHaveLength(1);
		expect(root.firstElementChild!.tagName).toBe('P');
		expect(container.querySelector('[data-state]')).toBeNull();
		expect(container.querySelector('[hidden]')).toBeNull();
	});
});
