import { render, screen } from '@testing-library/react';
import { LoadingState } from '@/components/ui/LoadingState';

describe('LoadingState', () => {
	it('renders the spinner with role="status" when no message is provided', () => {
		render(<LoadingState />);
		expect(screen.getByRole('status')).toBeInTheDocument();
	});

	it('renders a visible message when one is provided', () => {
		render(<LoadingState message="Loading..." />);
		expect(screen.getByText('Loading...')).toBeInTheDocument();
	});

	it('renders custom message and icon when provided', () => {
		render(<LoadingState message="Fetching..." icon={<svg data-testid="custom" />} />);
		expect(screen.getByText('Fetching...')).toBeInTheDocument();
		expect(screen.getByTestId('custom')).toBeInTheDocument();
	});
});
