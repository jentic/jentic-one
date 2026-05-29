import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { DiscoverEmptyState } from '@/components/discovery/DiscoverEmptyState';

function renderWithRouter(ui: React.ReactElement) {
	return render(ui, {
		wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter>,
	});
}

describe('DiscoverEmptyState', () => {
	describe('cold-start', () => {
		it('clicking "Browse the Jentic public catalog" calls onSwitchToDirectory', () => {
			const onSwitchToDirectory = vi.fn();
			renderWithRouter(
				<DiscoverEmptyState
					variant="cold-start"
					onSwitchToDirectory={onSwitchToDirectory}
				/>,
			);
			fireEvent.click(
				screen.getByRole('button', { name: /browse the jentic public catalog/i }),
			);
			expect(onSwitchToDirectory).toHaveBeenCalledTimes(1);
		});

		it('disables "Import from URL" when importHref is omitted', () => {
			const onSwitchToDirectory = vi.fn();
			renderWithRouter(
				<DiscoverEmptyState
					variant="cold-start"
					onSwitchToDirectory={onSwitchToDirectory}
				/>,
			);
			const importBtn = screen.getByRole('button', { name: /import from url/i });
			expect(importBtn).toBeDisabled();
			expect(importBtn).toHaveAttribute('aria-disabled', 'true');
			expect(importBtn).toHaveAttribute('title', 'Coming soon');
		});

		it('renders "Import from URL" as a link to importHref when provided', () => {
			const onSwitchToDirectory = vi.fn();
			renderWithRouter(
				<DiscoverEmptyState
					variant="cold-start"
					onSwitchToDirectory={onSwitchToDirectory}
					importHref="/apis/new"
				/>,
			);
			const link = screen.getByRole('link', { name: /import from url/i });
			expect(link).toHaveAttribute('href', '/apis/new');
		});

		it('renders the cold-start title and body copy', () => {
			renderWithRouter(
				<DiscoverEmptyState variant="cold-start" onSwitchToDirectory={() => {}} />,
			);
			expect(screen.getByText('Nothing in your workspace yet')).toBeInTheDocument();
			expect(
				screen.getByText('Try the Jentic public catalog or import an API from URL'),
			).toBeInTheDocument();
		});
	});

	describe('zero-search', () => {
		it('title contains the query', () => {
			renderWithRouter(<DiscoverEmptyState variant="zero-search" query="strpie" />);
			expect(screen.getByText(/no apis found for "strpie"/i)).toBeInTheDocument();
		});

		it('shows helpful description', () => {
			renderWithRouter(<DiscoverEmptyState variant="zero-search" query="emptycase" />);
			expect(
				screen.getByText(/try a different name or check for typos/i),
			).toBeInTheDocument();
		});
	});

	describe('filtered-empty', () => {
		it('clicking "Clear filters" calls onClearFilters', () => {
			const onClearFilters = vi.fn();
			renderWithRouter(
				<DiscoverEmptyState variant="filtered-empty" onClearFilters={onClearFilters} />,
			);
			fireEvent.click(screen.getByRole('button', { name: /clear filters/i }));
			expect(onClearFilters).toHaveBeenCalledTimes(1);
		});

		it('renders the filtered-empty title and body copy', () => {
			renderWithRouter(
				<DiscoverEmptyState variant="filtered-empty" onClearFilters={() => {}} />,
			);
			expect(screen.getByText('No APIs match the current filters')).toBeInTheDocument();
			expect(
				screen.getByText('Adjust the filters above or clear them to see all APIs.'),
			).toBeInTheDocument();
		});
	});
});
