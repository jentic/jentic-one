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

	it('renders an inline edit button when onEdit is set and fires it on click', async () => {
		const user = userEvent.setup();
		const onEdit = vi.fn();
		renderWithProviders(
			<PageHeader
				title="GitHub Tools"
				onEdit={onEdit}
				editLabel="Rename toolkit"
				animated={false}
			/>,
		);
		const editButton = screen.getByRole('button', { name: 'Rename toolkit' });
		expect(editButton).toBeInTheDocument();
		await user.click(editButton);
		expect(onEdit).toHaveBeenCalledOnce();
	});

	it('renders no edit button when onEdit is omitted', () => {
		renderWithProviders(<PageHeader title="GitHub Tools" animated={false} />);
		expect(screen.queryByRole('button', { name: 'Rename toolkit' })).not.toBeInTheDocument();
	});

	it('vertically centers the actions cluster so single-line callers are not shifted (#13)', () => {
		// The cluster class was silently flipped to `self-start`, nudging the
		// actions/Edit pill up on every caller. Pin `self-center` so the
		// alignment can't regress again without a test failing.
		renderWithProviders(
			<PageHeader
				title="GitHub Tools"
				onEdit={() => {}}
				editLabel="Rename toolkit"
				animated={false}
			/>,
		);
		const cluster = screen.getByTestId('page-header-actions');
		expect(cluster).toHaveClass('self-center');
		expect(cluster).not.toHaveClass('self-start');
	});

	it('does not truncate or set a title tooltip on the heading', () => {
		renderWithProviders(<PageHeader title="A very long title" animated={false} />);
		const heading = screen.getByRole('heading', { level: 1 });
		expect(heading).not.toHaveClass('truncate');
		expect(heading).not.toHaveAttribute('title');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<PageHeader title="Dashboard" subtitle="Overview" animated={false} />,
		);
		await checkA11y(container);
	});
});
