import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { Toaster } from '@/shared/ui/Toaster';
import { toast, dismissToast, clearAllToasts } from '@/shared/ui/toastStore';

describe('Toaster + toastStore', () => {
	afterEach(() => {
		clearAllToasts();
	});

	it('renders a toast pushed via toast()', async () => {
		renderWithProviders(<Toaster />);
		toast({ title: 'Saved', description: 'Your changes were saved' });
		expect(await screen.findByText('Saved')).toBeInTheDocument();
		expect(screen.getByText('Your changes were saved')).toBeInTheDocument();
	});

	it('dedupes by stable id', async () => {
		renderWithProviders(<Toaster />);
		toast({ id: 'x', title: 'First' });
		toast({ id: 'x', title: 'Second' });
		expect(await screen.findByText('Second')).toBeInTheDocument();
		expect(screen.queryByText('First')).not.toBeInTheDocument();
	});

	it('dismisses via the close button', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Toaster />);
		toast({ id: 'd', title: 'Dismiss me' });
		await screen.findByText('Dismiss me');
		await user.click(screen.getByRole('button', { name: 'Dismiss' }));
		await waitFor(() => {
			expect(screen.queryByText('Dismiss me')).not.toBeInTheDocument();
		});
	});

	it('dismisses programmatically', async () => {
		renderWithProviders(<Toaster />);
		toast({ id: 'p', title: 'Bye' });
		await screen.findByText('Bye');
		dismissToast('p');
		await waitFor(() => {
			expect(screen.queryByText('Bye')).not.toBeInTheDocument();
		});
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Toaster />);
		toast({ title: 'Accessible toast' });
		await screen.findByText('Accessible toast');
		await checkA11y(container);
	});

	it('every variant is the same neutral card; only the icon is tinted, errors add a red bar', async () => {
		renderWithProviders(<Toaster />);
		for (const variant of ['success', 'info', 'warning', 'error'] as const) {
			toast({ id: variant, title: `${variant} title`, description: 'body', variant });
		}
		const cards = await screen.findAllByTestId('toast');
		expect(cards).toHaveLength(4);
		for (const card of cards) {
			expect(card).toHaveClass('bg-surface-sheet', 'border-hairline-field');
			expect(card.className).not.toMatch(/emerald|rose|bg-(success|danger|warning)\//);
			expect(screen.getByText(`${card.dataset.variant} title`)).toHaveClass(
				'text-foreground',
			);
		}
		const bars = screen.getAllByTestId('toast-accent-bar');
		expect(bars).toHaveLength(1);
		expect(bars[0].closest('[data-testid="toast"]')).toHaveAttribute('data-variant', 'error');
		const errorIcon = cards
			.find((c) => c.dataset.variant === 'error')
			?.querySelector('[data-testid="toast-icon"]');
		expect(errorIcon).toHaveClass('text-danger');
	});

	it('renders the action as a borderless tonal button', async () => {
		renderWithProviders(<Toaster />);
		toast({ title: 'Removed', action: { label: 'Undo', onClick: () => {} } });
		const undo = await screen.findByRole('button', { name: 'Undo' });
		expect(undo).toHaveAttribute('data-variant', 'tonal');
		expect(undo.className).not.toMatch(/control-edge/);
		expect(getComputedStyle(undo).borderTopWidth).toBe('0px');
	});
});
