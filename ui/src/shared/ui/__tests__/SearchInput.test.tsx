import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { SearchInput } from '@/shared/ui/SearchInput';

describe('SearchInput', () => {
	it('reports typed value via onValueChange', async () => {
		const user = userEvent.setup();
		const onValueChange = vi.fn();
		renderWithProviders(
			<SearchInput aria-label="Search" value="" onValueChange={onValueChange} />,
		);
		await user.type(screen.getByLabelText('Search'), 'a');
		expect(onValueChange).toHaveBeenCalledWith('a');
	});

	it('shows a clear button that empties the value', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [value, setValue] = useState('query');
			return <SearchInput aria-label="Search" value={value} onValueChange={setValue} />;
		}
		renderWithProviders(<Harness />);
		expect(screen.getByLabelText('Search')).toHaveValue('query');
		await user.click(screen.getByRole('button', { name: 'Clear search' }));
		expect(screen.getByLabelText('Search')).toHaveValue('');
	});

	it('clears on Escape', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [value, setValue] = useState('query');
			return <SearchInput aria-label="Search" value={value} onValueChange={setValue} />;
		}
		renderWithProviders(<Harness />);
		const input = screen.getByLabelText('Search');
		input.focus();
		await user.keyboard('{Escape}');
		expect(input).toHaveValue('');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<SearchInput aria-label="Search the catalog" value="" onValueChange={() => {}} />,
		);
		await checkA11y(container);
	});
});
