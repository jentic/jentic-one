import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { LoadingState } from '@/shared/ui/LoadingState';

describe('LoadingState', () => {
	it('exposes a polite status region', () => {
		renderWithProviders(<LoadingState message="Loading data" />);
		const status = screen.getByRole('status');
		expect(status).toHaveAttribute('aria-live', 'polite');
		expect(screen.getByText('Loading data')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<LoadingState message="Loading" description="Hang tight" />,
		);
		await checkA11y(container);
	});
});
