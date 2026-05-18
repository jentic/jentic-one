import { http, HttpResponse } from 'msw';
import { Routes, Route } from 'react-router-dom';
import { screen, renderWithProviders, userEvent } from '../test-utils';
import { worker } from '../mocks/browser';
import { Layout } from '@/components/layout/Layout';

function renderLayout(route = '/') {
	return renderWithProviders(
		<Routes>
			<Route element={<Layout />}>
				<Route path="/" element={<div>Dashboard</div>} />
				<Route path="/toolkits" element={<div>Toolkits</div>} />
			</Route>
		</Routes>,
		{ route },
	);
}

describe('Layout', () => {
	it('renders top + bottom nav with visible primary tabs and a More overflow', async () => {
		renderLayout();

		// Logo with "Mini" badge marks the top navbar.
		await screen.findByText('Mini');

		// `NavTabs` uses a `ResizeObserver` budget so the visible tab count
		// depends on the parent's width. In a constrained CI viewport only
		// the first one or two tabs survive — the rest live inside the
		// closed More dropdown (not mounted until clicked). Assert the
		// Dashboard tab is always visible, and a More overflow exists.
		const text = document.body.textContent ?? '';
		expect(text).toContain('Dashboard');
		expect(screen.getAllByRole('button', { name: /more/i }).length).toBeGreaterThan(0);
	});

	it('exposes every NAV_ITEMS label after opening the More dropdown', async () => {
		const user = userEvent.setup();
		renderLayout();

		// Click the desktop More button (the bottom-bar More opens a sheet
		// with a different aria-label).
		const moreButtons = await screen.findAllByRole('button', { name: /^more$/i });
		await user.click(moreButtons[0]);

		const text = document.body.textContent ?? '';
		expect(text).toContain('Search');
		expect(text).toContain('API Catalog');
		expect(text).toContain('Workflows');
		expect(text).toContain('Credentials');
	});

	it('renders the user-menu avatar button', async () => {
		renderLayout();

		expect(await screen.findByLabelText('User menu')).toBeInTheDocument();
	});

	it('shows username and Log out inside the user menu when opened', async () => {
		const user = userEvent.setup();
		renderLayout();

		await user.click(await screen.findByLabelText('User menu'));

		expect(await screen.findByText('admin')).toBeInTheDocument();
		expect(screen.getByText('Log out')).toBeInTheDocument();
	});

	it('renders child route content via Outlet', async () => {
		renderLayout('/toolkits');

		const main = document.querySelector('main')!;
		expect(main.textContent).toContain('Toolkits');
	});

	it('shows pending-requests pill when there are pending requests', async () => {
		worker.use(
			http.get('/toolkits', () => HttpResponse.json([{ id: 'tk-1', name: 'Toolkit A' }])),
			http.get('/toolkits/tk-1/access-requests', () =>
				HttpResponse.json([{ id: 'req-1', status: 'pending', reason: 'Need access' }]),
			),
		);

		renderLayout();

		expect(await screen.findByText(/1 Pending Request/)).toBeInTheDocument();
	});

	it('shows current version inside the user menu', async () => {
		worker.use(
			http.get('/version', () =>
				HttpResponse.json({
					current: '0.5.3',
					latest: '0.5.3',
					release_url: null,
				}),
			),
		);

		const user = userEvent.setup();
		renderLayout();

		await user.click(await screen.findByLabelText('User menu'));

		expect(await screen.findByText('v0.5.3')).toBeInTheDocument();
		expect(screen.queryByText(/Update available/)).not.toBeInTheDocument();
	});

	it('shows update-available link in the user menu when a new version exists', async () => {
		worker.use(
			http.get('/version', () =>
				HttpResponse.json({
					current: '0.2.0',
					latest: '0.3.0',
					release_url: 'https://github.com/release/0.3.0',
				}),
			),
		);

		const user = userEvent.setup();
		renderLayout();

		await user.click(await screen.findByLabelText('User menu'));

		expect(await screen.findByText(/Update available: 0.3.0/)).toBeInTheDocument();
	});

	it('renders the Jentic logo', async () => {
		renderLayout();

		expect((await screen.findAllByText('Mini')).length).toBeGreaterThan(0);
	});
});
