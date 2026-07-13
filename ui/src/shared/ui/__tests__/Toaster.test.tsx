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
});
