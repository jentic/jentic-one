import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { PageHeader } from '@/shared/ui/PageHeader';
import { Button } from '@/shared/ui/Button';

describe('PageHeader', () => {
	it('renders the title as a level-1 heading', () => {
		renderWithProviders(<PageHeader title="Dashboard" animated={false} />);
		expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
	});

	it('renders subtitle and actions', () => {
		renderWithProviders(
			<PageHeader
				title="APIs"
				subtitle="Browse the catalog"
				actions={<Button>New API</Button>}
				animated={false}
			/>,
		);
		expect(screen.getByText('Browse the catalog')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'New API' })).toBeInTheDocument();
	});

	it('fires action callbacks', async () => {
		const user = userEvent.setup();
		const onClick = vi.fn();
		renderWithProviders(
			<PageHeader
				title="APIs"
				actions={<Button onClick={onClick}>Action</Button>}
				animated={false}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'Action' }));
		expect(onClick).toHaveBeenCalledOnce();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<PageHeader title="Dashboard" subtitle="Overview" animated={false} />,
		);
		await checkA11y(container);
	});
});
