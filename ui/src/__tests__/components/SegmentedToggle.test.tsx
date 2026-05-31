import { render, screen, fireEvent } from '@testing-library/react';
import axe from 'axe-core';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';

const OPTIONS = [
	{ value: 'all', label: 'All' },
	{ value: 'a', label: 'Alpha' },
	{ value: 'b', label: 'Beta' },
];

describe('SegmentedToggle', () => {
	it('renders every option as a button', () => {
		render(<SegmentedToggle layoutId="t1" value="all" onChange={() => {}} options={OPTIONS} />);
		expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Alpha' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Beta' })).toBeInTheDocument();
	});

	it('calls onChange with the picked value', () => {
		const onChange = vi.fn();
		render(<SegmentedToggle layoutId="t2" value="all" onChange={onChange} options={OPTIONS} />);
		fireEvent.click(screen.getByRole('button', { name: 'Beta' }));
		expect(onChange).toHaveBeenCalledWith('b');
	});

	it('marks only the active segment as non-interactive (cursor)', () => {
		render(<SegmentedToggle layoutId="t3" value="a" onChange={() => {}} options={OPTIONS} />);
		// The non-active buttons get `cursor-pointer`; the active one does not.
		expect(screen.getByRole('button', { name: 'Alpha' }).className).not.toContain(
			'cursor-pointer',
		);
		expect(screen.getByRole('button', { name: 'All' }).className).toContain('cursor-pointer');
		expect(screen.getByRole('button', { name: 'Beta' }).className).toContain('cursor-pointer');
	});

	it('has no accessibility violations', async () => {
		const { container } = render(
			<SegmentedToggle layoutId="t4" value="all" onChange={() => {}} options={OPTIONS} />,
		);
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});
});
