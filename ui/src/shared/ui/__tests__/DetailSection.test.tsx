import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { Shield } from 'lucide-react';
import { DetailSection, EmptyRow } from '@/shared/ui/DetailSection';

describe('DetailSection', () => {
	it('renders the heading, icon medallion, and children', () => {
		renderWithProviders(
			<DetailSection title="Bound toolkits" icon={<Shield className="h-4 w-4" />}>
				<p>body</p>
			</DetailSection>,
		);
		expect(screen.getByText('Bound toolkits')).toBeInTheDocument();
		expect(screen.getByText('body')).toBeInTheDocument();
	});

	it('wires the header action button', async () => {
		const user = userEvent.setup();
		const onClick = vi.fn();
		renderWithProviders(
			<DetailSection
				title="Keys"
				action={{ label: 'New key', onClick, ariaLabel: 'Create a new key' }}
			>
				<p>body</p>
			</DetailSection>,
		);
		await user.click(screen.getByRole('button', { name: 'Create a new key' }));
		expect(onClick).toHaveBeenCalledOnce();
	});

	it('renders trailing content when a button does not fit', () => {
		renderWithProviders(
			<DetailSection title="Recent" trailing={<a href="/monitor">Open Monitor</a>}>
				<p>body</p>
			</DetailSection>,
		);
		expect(screen.getByRole('link', { name: 'Open Monitor' })).toBeInTheDocument();
	});

	it('is accessible (with an EmptyRow body)', async () => {
		const { container } = renderWithProviders(
			<DetailSection title="Scopes" icon={<Shield className="h-4 w-4" />}>
				<EmptyRow icon={<Shield />}>No scopes granted.</EmptyRow>
			</DetailSection>,
		);
		expect(screen.getByText('No scopes granted.')).toBeInTheDocument();
		await checkA11y(container);
	});
});
