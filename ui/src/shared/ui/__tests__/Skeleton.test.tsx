import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Skeleton, SkeletonRows } from '@/shared/ui/Skeleton';

describe('Skeleton', () => {
	it('renders a placeholder element', () => {
		renderWithProviders(<Skeleton data-testid="sk" className="h-4 w-20" />);
		expect(screen.getByTestId('sk')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Skeleton className="h-4 w-20" />);
		await checkA11y(container);
	});
});

describe('SkeletonRows', () => {
	it('exposes a polite busy status with a screen-reader label', () => {
		renderWithProviders(<SkeletonRows />);
		const status = screen.getByRole('status');
		expect(status).toHaveAttribute('aria-busy', 'true');
		expect(status).toHaveAttribute('aria-live', 'polite');
		// Announced once to assistive tech, hidden visually.
		expect(screen.getByText('Loading…')).toBeInTheDocument();
	});

	it('renders the requested number of rows', () => {
		const { container } = renderWithProviders(<SkeletonRows rows={5} />);
		// Each row is a direct child of the status container (the sr-only span is
		// the first child), so count children minus that label node.
		const status = screen.getByRole('status');
		const rowCount = status.querySelectorAll(':scope > div').length;
		expect(rowCount).toBe(5);
		expect(container).toBeTruthy();
	});

	it('defaults to three rows', () => {
		renderWithProviders(<SkeletonRows />);
		const status = screen.getByRole('status');
		expect(status.querySelectorAll(':scope > div').length).toBe(3);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<SkeletonRows rows={3} />);
		await checkA11y(container);
	});
});
