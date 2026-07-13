import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { PageHelp } from '@/shared/ui/PageHelp';

describe('PageHelp', () => {
	it('renders a trigger button that opens the dialog', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<PageHelp title="About Discover" intro="Find and import APIs." bindShortcut={false} />,
		);
		expect(screen.queryByTestId('page-help-content')).not.toBeVisible();
		await user.click(screen.getByTestId('page-help-trigger'));
		expect(screen.getByTestId('page-help-content')).toBeVisible();
		expect(screen.getByText('Find and import APIs.')).toBeInTheDocument();
	});

	it('renders sections, shortcuts and links', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<PageHelp
				title="Help"
				sections={[{ heading: 'How to', body: <p>Do the thing</p> }]}
				shortcuts={[{ keys: ['⌘', 'K'], label: 'Search', chord: true }]}
				links={[{ href: 'https://docs.example.com', label: 'Docs' }]}
				bindShortcut={false}
			/>,
		);
		await user.click(screen.getByTestId('page-help-trigger'));
		expect(screen.getByText('How to')).toBeInTheDocument();
		expect(screen.getByText('Do the thing')).toBeInTheDocument();
		expect(screen.getByTestId('page-help-shortcuts')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('target', '_blank');
	});

	it('opens via the ⌘ / shortcut when bound', async () => {
		const user = userEvent.setup();
		renderWithProviders(<PageHelp title="Help" intro="Hi" />);
		await user.keyboard('{Meta>}/{/Meta}');
		expect(screen.getByTestId('page-help-content')).toBeVisible();
	});

	it('has no critical a11y violations when open', async () => {
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<PageHelp title="About" intro="Some help text." bindShortcut={false} />,
		);
		await user.click(screen.getByTestId('page-help-trigger'));
		await checkA11y(container);
	});
});
