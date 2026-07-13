import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';

describe('ErrorAlert', () => {
	it('renders a string message in an alert region', () => {
		renderWithProviders(<ErrorAlert message="Something failed" />);
		expect(screen.getByRole('alert')).toHaveTextContent('Something failed');
	});

	it('renders an Error instance message', () => {
		renderWithProviders(<ErrorAlert message={new Error('Boom')} />);
		expect(screen.getByRole('alert')).toHaveTextContent('Boom');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<ErrorAlert message="Network error" />);
		await checkA11y(container);
	});
});
