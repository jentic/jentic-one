import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Pagination } from '@/shared/ui/Pagination';

describe('Pagination', () => {
	it('renders the page indicator', () => {
		renderWithProviders(<Pagination page={2} totalPages={5} onPageChange={() => {}} />);
		expect(screen.getByText('Page 2 of 5')).toBeInTheDocument();
	});

	it('disables Previous on the first page', () => {
		renderWithProviders(<Pagination page={1} totalPages={5} onPageChange={() => {}} />);
		expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
	});

	it('disables Next on the last page', () => {
		renderWithProviders(<Pagination page={5} totalPages={5} onPageChange={() => {}} />);
		expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
	});

	it('calls onPageChange when navigating', async () => {
		const user = userEvent.setup();
		const onPageChange = vi.fn();
		renderWithProviders(<Pagination page={2} totalPages={5} onPageChange={onPageChange} />);
		await user.click(screen.getByRole('button', { name: 'Next page' }));
		expect(onPageChange).toHaveBeenCalledWith(3);
	});

	it('renders the range summary when totalCount/pageSize are provided', () => {
		renderWithProviders(
			<Pagination
				page={2}
				totalPages={5}
				totalCount={50}
				pageSize={10}
				onPageChange={() => {}}
			/>,
		);
		expect(screen.getByText(/11–20 of 50/)).toBeInTheDocument();
	});

	it('renders nothing when there are no pages', () => {
		const { container } = renderWithProviders(
			<Pagination page={1} totalPages={0} onPageChange={() => {}} />,
		);
		expect(container).toBeEmptyDOMElement();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<Pagination page={2} totalPages={5} onPageChange={() => {}} />,
		);
		await checkA11y(container);
	});
});
