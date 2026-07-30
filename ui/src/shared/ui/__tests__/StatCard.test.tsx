import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Activity } from 'lucide-react';
import { StatCard } from '@/shared/ui/StatCard';

describe('StatCard', () => {
	it('renders label, value and caption', () => {
		renderWithProviders(
			<StatCard
				label="Executions"
				value="1,204"
				caption="last 7 days"
				icon={<Activity className="h-4 w-4" />}
				accent="blue"
			/>,
		);
		expect(screen.getByText('Executions')).toBeInTheDocument();
		expect(screen.getByText('1,204')).toBeInTheDocument();
		expect(screen.getByText('last 7 days')).toBeInTheDocument();
	});

	it('renders a link when href is set and the tile is interactive', () => {
		renderWithProviders(<StatCard label="Agents" value={3} href="/agents" />);
		expect(screen.getByRole('link', { name: 'Agents' })).toHaveAttribute(
			'href',
			expect.stringContaining('/agents'),
		);
	});

	it('shows the degraded state instead of the value on error', () => {
		renderWithProviders(<StatCard label="Usage" value="9" error="Unavailable" />);
		expect(screen.getByRole('alert')).toHaveTextContent('Unavailable');
		expect(screen.queryByText('9')).not.toBeInTheDocument();
	});

	it('shows a skeleton while loading (no value, no link)', () => {
		renderWithProviders(<StatCard label="Usage" value="9" isLoading href="/x" />);
		expect(screen.queryByText('9')).not.toBeInTheDocument();
		expect(screen.queryByRole('link')).not.toBeInTheDocument();
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<StatCard label="Executions" value="12" caption="7d" />,
		);
		await checkA11y(container);
	});
});
