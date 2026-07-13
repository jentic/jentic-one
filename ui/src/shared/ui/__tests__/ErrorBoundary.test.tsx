import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';

function Boom(): never {
	throw new Error('kaboom');
}

describe('ErrorBoundary', () => {
	it('renders children when there is no error', () => {
		renderWithProviders(
			<ErrorBoundary>
				<p>All good</p>
			</ErrorBoundary>,
		);
		expect(screen.getByText('All good')).toBeInTheDocument();
	});

	it('renders the fallback UI when a child throws', () => {
		renderWithProviders(
			<ErrorBoundary>
				<Boom />
			</ErrorBoundary>,
		);
		expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
		expect(screen.getByText('kaboom')).toBeInTheDocument();
	});

	it('renders a custom fallback when provided', () => {
		renderWithProviders(
			<ErrorBoundary fallback={<p>Custom fallback</p>}>
				<Boom />
			</ErrorBoundary>,
		);
		expect(screen.getByText('Custom fallback')).toBeInTheDocument();
	});

	it('exposes a working Try again button', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<ErrorBoundary>
				<Boom />
			</ErrorBoundary>,
		);
		// The button exists in the default fallback.
		expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Try again' }));
	});

	it('has no critical a11y violations in the fallback', async () => {
		const { container } = renderWithProviders(
			<ErrorBoundary>
				<Boom />
			</ErrorBoundary>,
		);
		await checkA11y(container);
	});
});
