import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { LoadingSpinner } from '@/shared/ui/LoadingSpinner';

describe('LoadingSpinner', () => {
	it('renders text when provided', () => {
		renderWithProviders(<LoadingSpinner text="Please wait" />);
		expect(screen.getByText('Please wait')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<LoadingSpinner text="Loading" description="Fetching results" />,
		);
		await checkA11y(container);
	});
});
