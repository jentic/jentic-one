import { describe, it, expect } from 'vitest';
import { renderWithProviders } from '@/__tests__/test-utils';
import { AreaSparkline } from '@/shared/ui/charts/AreaSparkline';

describe('AreaSparkline', () => {
	it('draws a decorative line over a filled area', () => {
		const { container } = renderWithProviders(<AreaSparkline data={[1, 4, 2, 8]} />);
		const svg = container.querySelector('svg');
		expect(svg).toHaveAttribute('aria-hidden', 'true');
		const [area, line] = container.querySelectorAll('path');
		expect(line.getAttribute('d')).toMatch(/^M 0\.0,/);
		// The area is the line closed along the bottom edge.
		expect(area.getAttribute('d')).toBe(`${line.getAttribute('d')} L 96,28 L 0,28 Z`);
	});

	it('peaks the largest value at the top of the box', () => {
		const { container } = renderWithProviders(<AreaSparkline data={[0, 10]} />);
		const line = container.querySelectorAll('path')[1];
		expect(line.getAttribute('d')).toBe('M 0.0,26.0 L 96.0,2.0');
	});

	it('runs all-zero data flat along the bottom', () => {
		const { container } = renderWithProviders(<AreaSparkline data={[0, 0, 0]} />);
		const line = container.querySelectorAll('path')[1];
		expect(line.getAttribute('d')).toBe('M 0.0,26.0 L 48.0,26.0 L 96.0,26.0');
	});

	it('draws nothing for fewer than two points', () => {
		const { container } = renderWithProviders(<AreaSparkline data={[5]} />);
		expect(container.querySelector('svg')).toBeNull();
	});

	it('gives each instance its own gradient and clip ids', () => {
		const { container } = renderWithProviders(
			<>
				<AreaSparkline data={[1, 2]} />
				<AreaSparkline data={[2, 1]} />
			</>,
		);
		const ids = [...container.querySelectorAll('linearGradient, clipPath')].map((el) => el.id);
		expect(new Set(ids).size).toBe(4);
	});
});
