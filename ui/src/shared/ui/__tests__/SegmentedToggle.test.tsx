import { useState } from 'react';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { SegmentedToggle } from '@/shared/ui/SegmentedToggle';

const options = [
	{ value: 'list', label: 'List' },
	{ value: 'grid', label: 'Grid' },
];

describe('SegmentedToggle', () => {
	it('changes selection on click', async () => {
		const user = userEvent.setup();
		function Harness() {
			const [value, setValue] = useState('list');
			return (
				<SegmentedToggle
					options={options}
					value={value}
					onChange={setValue}
					layoutId="view"
				/>
			);
		}
		renderWithProviders(<Harness />);
		await user.click(screen.getByRole('button', { name: 'Grid' }));
		// The active segment text stays in the document after the switch.
		expect(screen.getByText('Grid')).toBeInTheDocument();
	});

	it('invokes onChange with the selected value', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(
			<SegmentedToggle options={options} value="list" onChange={onChange} layoutId="view2" />,
		);
		await user.click(screen.getByRole('button', { name: 'Grid' }));
		expect(onChange).toHaveBeenCalledWith('grid');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<SegmentedToggle options={options} value="list" onChange={() => {}} layoutId="view3" />,
		);
		await checkA11y(container);
	});

	it('keeps the sliding pill inside the control bounds for the whole animation', async () => {
		// INVARIANT (see the transition comment in SegmentedToggle): the pill
		// must never extend past the control's own bounds — an overshooting
		// spring transiently poked its right edge past the content width and
		// blipped a horizontal scrollbar in overflow-x-auto toolbar wrappers.
		// Sample the pill's rect against the root's on every animation frame
		// while switching to the far segment. Spring overshoot scales with the
		// step size, so the segments are deliberately wide and asymmetric
		// (worst case: a long jump onto a wide segment, like count labels).
		const user = userEvent.setup();
		const wide = [
			{ value: 'a', label: 'A 1' },
			{ value: 'b', label: 'B 12' },
			{ value: 'c', label: `C ${'1234567890'.repeat(4)}` },
		];
		function Harness() {
			const [value, setValue] = useState('a');
			return <SegmentedToggle options={wide} value={value} onChange={setValue} />;
		}
		const { container } = renderWithProviders(<Harness />);
		const root = container.querySelector('.overflow-x-clip') as HTMLElement;
		const pill = root?.querySelector('[aria-hidden="true"]') as HTMLElement;
		expect(root).not.toBeNull();
		expect(pill).not.toBeNull();

		await user.click(screen.getByRole('button', { name: wide[2].label }));

		const maxBleed = await new Promise<number>((resolve) => {
			let worst = -Infinity;
			const start = performance.now();
			function sample() {
				const rootRect = root.getBoundingClientRect();
				const pillRect = pill.getBoundingClientRect();
				worst = Math.max(
					worst,
					pillRect.right - rootRect.right,
					rootRect.left - pillRect.left,
				);
				if (performance.now() - start < 600) requestAnimationFrame(sample);
				else resolve(worst);
			}
			requestAnimationFrame(sample);
		});
		// The pill sits inside the root's 1px border + 2px padding; it must
		// never even reach the border box edge, let alone poke past it.
		expect(maxBleed).toBeLessThanOrEqual(0);
	});
});
