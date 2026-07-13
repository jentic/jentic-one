import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { EmptyState } from '@/shared/ui/EmptyState';
import { Button } from '@/shared/ui/Button';

describe('EmptyState', () => {
	it('renders the title, description and action', () => {
		renderWithProviders(
			<EmptyState
				icon={<span aria-hidden="true">∅</span>}
				title="Nothing here"
				description="Create your first item"
				action={<Button>New item</Button>}
			/>,
		);
		expect(screen.getByText('Nothing here')).toBeInTheDocument();
		expect(screen.getByText('Create your first item')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'New item' })).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<EmptyState icon={<span aria-hidden="true">∅</span>} title="Empty" />,
		);
		await checkA11y(container);
	});
});
