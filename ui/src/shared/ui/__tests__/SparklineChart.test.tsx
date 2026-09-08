import { renderWithProviders } from '@/__tests__/test-utils';
import { SparklineChart } from '@/shared/ui/charts/SparklineChart';

describe('SparklineChart', () => {
	it('renders a decorative svg path for a series', () => {
		const { container } = renderWithProviders(<SparklineChart data={[1, 4, 2, 8]} />);
		const svg = container.querySelector('svg');
		expect(svg).not.toBeNull();
		expect(svg).toHaveAttribute('aria-hidden', 'true');
		const path = container.querySelector('path');
		expect(path?.getAttribute('d')).toMatch(/^M /);
	});

	it('renders an empty placeholder when there are fewer than 2 points', () => {
		const { container } = renderWithProviders(<SparklineChart data={[5]} />);
		expect(container.querySelector('svg')).toBeNull();
	});

	it('uses an explicit stroke color when provided', () => {
		const { container } = renderWithProviders(
			<SparklineChart data={[1, 2, 3]} color="#ff0000" />,
		);
		expect(container.querySelector('path')).toHaveAttribute('stroke', '#ff0000');
	});
});
