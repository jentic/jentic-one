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

	it('defaults to the context-aware tone, edgeless (a filter, not a form field)', () => {
		renderWithProviders(<SearchInput aria-label="Search" value="" onValueChange={() => {}} />);
		const input = screen.getByLabelText('Search');
		expect(input.closest('[data-tone]')).toHaveAttribute('data-tone', 'default');
		expect(input).toHaveClass('border', 'border-transparent');
		expect(input).not.toHaveClass('border-control-edge');
	});

	it('`field` keeps the form-field resting edge', () => {
		renderWithProviders(
			<SearchInput aria-label="Search" value="" onValueChange={() => {}} field />,
		);
		const input = screen.getByLabelText('Search');
		expect(input).toHaveClass('border', 'border-control-edge');
		expect(input).not.toHaveClass('border-transparent');
	});

	it.each([
		['surface', 'bg-surface-1', 'h-9'],
		['inset', 'bg-surface-field', 'h-[34px]'],
	] as const)('tone="%s" is a borderless field on its surface', (tone, bg, height) => {
		renderWithProviders(
			<SearchInput aria-label="Filter" value="" onValueChange={() => {}} tone={tone} />,
		);
		const input = screen.getByLabelText('Filter');
		expect(input.closest('[data-tone]')).toHaveAttribute('data-tone', tone);
		expect(input).toHaveClass(bg, height, 'border-transparent');
		expect(input).not.toHaveClass('bg-card');
		// No visible edge at rest: the border is transparent.
		expect(getComputedStyle(input).borderTopColor).toBe('rgba(0, 0, 0, 0)');
	});

	it.each(['surface', 'inset'] as const)(
		'tone="%s" keeps the clear button and has no critical a11y violations',
		async (tone) => {
			const { container } = renderWithProviders(
				<div className="bg-surface-1 p-4">
					<SearchInput
						aria-label="Filter your APIs"
						placeholder="Filter by name…"
						value="abc"
						onValueChange={() => {}}
						tone={tone}
					/>
				</div>,
			);
			expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument();
			await checkA11y(container);
		},
	);
});
