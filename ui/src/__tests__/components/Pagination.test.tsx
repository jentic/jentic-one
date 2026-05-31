import { render, screen, fireEvent } from '@testing-library/react';
import axe from 'axe-core';
import { Pagination } from '@/components/ui/Pagination';

describe('Pagination', () => {
	it('displays page info and disables buttons at boundaries', () => {
		render(<Pagination page={1} totalPages={5} onPageChange={vi.fn()} />);
		expect(screen.getByText('Page 1 of 5')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();
		expect(screen.getByRole('button', { name: /next/i })).not.toBeDisabled();
	});

	it('renders as a nav element with aria-label', () => {
		render(<Pagination page={1} totalPages={5} onPageChange={vi.fn()} />);
		const nav = screen.getByRole('navigation', { name: /pagination/i });
		expect(nav).toBeInTheDocument();
	});

	it('announces page changes to screen readers', () => {
		render(<Pagination page={3} totalPages={5} onPageChange={vi.fn()} />);
		const indicator = screen.getByText('Page 3 of 5');
		expect(indicator).toHaveAttribute('aria-live', 'polite');
	});

	it('calls onPageChange with correct page numbers', () => {
		const onPageChange = vi.fn();
		render(<Pagination page={3} totalPages={5} onPageChange={onPageChange} />);

		fireEvent.click(screen.getByRole('button', { name: /previous/i }));
		expect(onPageChange).toHaveBeenCalledWith(2);

		fireEvent.click(screen.getByRole('button', { name: /next/i }));
		expect(onPageChange).toHaveBeenCalledWith(4);
	});

	it('returns null when totalPages <= 0', () => {
		const { container } = render(<Pagination page={1} totalPages={0} onPageChange={vi.fn()} />);
		expect(container.firstChild).toBeNull();
	});

	it('disables next button on last page', () => {
		render(<Pagination page={5} totalPages={5} onPageChange={vi.fn()} />);
		expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
		expect(screen.getByRole('button', { name: /previous/i })).not.toBeDisabled();
	});

	it('disables both buttons on single-page result', () => {
		render(<Pagination page={1} totalPages={1} onPageChange={vi.fn()} />);
		expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();
		expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
	});

	it('has no accessibility violations', async () => {
		const { container } = render(<Pagination page={2} totalPages={5} onPageChange={vi.fn()} />);
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});

	// ── Range-summary variant (webapp-style) ─────────────────────────────────

	describe('range-summary variant', () => {
		it('renders an "X–Y of N" range when totalCount + pageSize are provided', () => {
			render(
				<Pagination
					page={2}
					totalPages={5}
					totalCount={123}
					pageSize={24}
					onPageChange={vi.fn()}
				/>,
			);
			expect(screen.getByText('25–48 of 123')).toBeInTheDocument();
			expect(screen.getByText('Page 2 of 5')).toBeInTheDocument();
			expect(screen.getByRole('button', { name: /previous page/i })).toBeInTheDocument();
			expect(screen.getByRole('button', { name: /next page/i })).toBeInTheDocument();
		});

		it('clamps the range end to the total count on the last page', () => {
			render(
				<Pagination
					page={5}
					totalPages={5}
					totalCount={107}
					pageSize={24}
					onPageChange={vi.fn()}
				/>,
			);
			expect(screen.getByText('97–107 of 107')).toBeInTheDocument();
		});

		it('disables boundary buttons in the range variant', () => {
			render(
				<Pagination
					page={1}
					totalPages={3}
					totalCount={50}
					pageSize={24}
					onPageChange={vi.fn()}
				/>,
			);
			expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled();
			expect(screen.getByRole('button', { name: /next page/i })).not.toBeDisabled();
		});

		it('falls back to the simple variant when totalCount is omitted', () => {
			render(<Pagination page={2} totalPages={5} pageSize={24} onPageChange={vi.fn()} />);
			expect(screen.queryByText(/of 123/)).toBeNull();
			expect(screen.getByRole('button', { name: /previous/i })).toBeInTheDocument();
			expect(screen.getByRole('button', { name: /next/i })).toBeInTheDocument();
		});
	});
});
