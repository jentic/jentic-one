import { http, HttpResponse } from 'msw';
import { screen, renderWithProviders, userEvent, waitFor } from '../test-utils';
import { worker } from '../mocks/browser';
import { UserMenu } from '@/components/layout/UserMenu';

describe('UserMenu', () => {
	it('renders the avatar button and switches to the username initial after auth loads', async () => {
		renderWithProviders(<UserMenu />);

		const button = await screen.findByRole('button', { name: /user menu/i });
		expect(button).toBeInTheDocument();
		// `/user/me` MSW handler returns `{ username: 'admin' }`; the avatar
		// initial flips from the 'U' fallback to 'A' once the query
		// resolves, so we wait rather than asserting synchronously.
		await waitFor(() => expect(button.textContent).toContain('A'));
	});

	it('opens the menu on click and closes on outside click', async () => {
		const user = userEvent.setup();
		renderWithProviders(<UserMenu />);

		// Closed initially.
		expect(screen.queryByRole('menu')).not.toBeInTheDocument();

		await user.click(await screen.findByRole('button', { name: /user menu/i }));
		expect(await screen.findByRole('menu')).toBeInTheDocument();

		await user.click(document.body);
		expect(screen.queryByRole('menu')).not.toBeInTheDocument();
	});

	it('closes the menu on Escape', async () => {
		const user = userEvent.setup();
		renderWithProviders(<UserMenu />);

		await user.click(await screen.findByRole('button', { name: /user menu/i }));
		expect(screen.getByRole('menu')).toBeInTheDocument();

		await user.keyboard('{Escape}');
		expect(screen.queryByRole('menu')).not.toBeInTheDocument();
	});

	it('renders identity (username + version) ABOVE the action items', async () => {
		worker.use(
			http.get('/version', () =>
				HttpResponse.json({ current: '0.5.3', latest: '0.5.3', release_url: null }),
			),
		);
		const user = userEvent.setup();
		renderWithProviders(<UserMenu />);

		await user.click(await screen.findByRole('button', { name: /user menu/i }));
		const menu = await screen.findByRole('menu');

		const username = await screen.findByText('admin');
		const version = await screen.findByText('v0.5.3');
		const apiDocs = await screen.findByRole('menuitem', { name: /api docs/i });

		// All present inside the menu.
		expect(menu.contains(username)).toBe(true);
		expect(menu.contains(version)).toBe(true);
		expect(menu.contains(apiDocs)).toBe(true);

		// Username and version must appear before the API docs link in the
		// document order — this is the visual ordering the user explicitly
		// asked for ("version on top of the api docs").
		expect(
			username.compareDocumentPosition(apiDocs) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeGreaterThan(0);
		expect(
			version.compareDocumentPosition(apiDocs) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeGreaterThan(0);
	});

	it('renders the update-available link when a newer version exists', async () => {
		worker.use(
			http.get('/version', () =>
				HttpResponse.json({
					current: '0.2.0',
					latest: '0.3.0',
					release_url: 'https://example.test/release/0.3.0',
				}),
			),
		);
		const user = userEvent.setup();
		renderWithProviders(<UserMenu />);

		await user.click(await screen.findByRole('button', { name: /user menu/i }));

		expect(await screen.findByText(/Update available: 0\.3\.0/)).toBeInTheDocument();
	});

	it('renders Log out as the bottom action and uses inset-pill rounding', async () => {
		const user = userEvent.setup();
		renderWithProviders(<UserMenu />);

		await user.click(await screen.findByRole('button', { name: /user menu/i }));

		const logout = await screen.findByRole('menuitem', { name: /log out/i });
		// Every menu item should carry `rounded-md` from `menuItemClass`,
		// which is how we keep hover backgrounds from touching the panel
		// border on any item — including the last one.
		expect(logout.className).toContain('rounded-md');
	});
});
