/**
 * The "Can call" list⇄cards view toggle, exercised through the Agents page: it
 * only shows once the agent has rows, switches the layout, persists the choice
 * to localStorage, and the cards grid's add-API tile drives the same Add-APIs
 * flow (disabled, with the reason, when the viewer can't bind).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	act,
	fireEvent,
	renderWithProviders,
	screen,
	within,
	waitFor,
	userEvent,
	settleAnimations,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster, TOOLTIP_DELAY_MS } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { API_VIEW_STORAGE_KEY } from '@/modules/agents/lib/apiView';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function seedViewer(permissions: string[]) {
	worker.use(
		http.get('/users/me', () =>
			HttpResponse.json({
				id: 'usr_viewer_1',
				email: 'viewer@local',
				first_name: 'View',
				last_name: 'Er',
				active: true,
				permissions,
				must_change_password: false,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: null,
			}),
		),
	);
}

function renderPage(route = '/?agent=agnt_active_1') {
	return renderWithProviders(
		<AuthProvider>
			<AgentsPage />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

/** The default `agnt_active_1` fixture carries two live bindings → a toolbar. */
async function waitForToolbar() {
	await screen.findByText('Slack');
	return screen.findByTestId('can-call-toolbar');
}

describe('Can call — list⇄cards view toggle', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		resetAgentsStore();
		resetCredentialsStore();
		window.localStorage.clear();
	});
	afterEach(() => {
		setToken(null);
		window.localStorage.clear();
	});

	it('defaults to list, shows the rows, and offers the toggle', async () => {
		seedViewer(['org:admin']);
		const { container } = renderPage();
		const toolbar = await waitForToolbar();

		expect(within(toolbar).getByRole('radio', { name: 'List view' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		// List view: the rows render as api-tile, no cards grid.
		expect(screen.getAllByTestId('api-tile').length).toBeGreaterThan(0);
		expect(screen.queryByTestId('can-call-cards')).toBeNull();
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('switches to cards, renders cards, and persists the choice', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		const { container } = renderPage();
		const toolbar = await waitForToolbar();

		await user.click(within(toolbar).getByRole('radio', { name: 'Cards view' }));

		expect(await screen.findByTestId('can-call-cards')).toBeInTheDocument();
		expect(screen.getAllByTestId('api-card').length).toBeGreaterThan(0);
		// The list rows are gone while cards are up.
		await waitFor(() => expect(screen.queryByTestId('api-tile')).toBeNull());
		expect(window.localStorage.getItem(API_VIEW_STORAGE_KEY)).toBe('cards');
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('keeps only the toggle on the toolbar — no API/credential count', async () => {
		seedViewer(['org:admin']);
		renderPage();
		const toolbar = await waitForToolbar();
		expect(within(toolbar).getByRole('radiogroup')).toBeInTheDocument();
		expect(toolbar).not.toHaveTextContent(/\bAPIs?\b.*·/);
		expect(toolbar).not.toHaveTextContent(/credentials?/);
	});

	it('a stored cards choice survives a remount, and each card reads its row’s status', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		const first = renderPage();
		const toolbar = await waitForToolbar();
		const rowStatuses = screen
			.getAllByTestId('tile-status-chip')
			.map((el) => el.getAttribute('data-status'));

		await user.click(within(toolbar).getByRole('radio', { name: 'Cards view' }));
		await screen.findByTestId('can-call-cards');
		first.unmount();

		renderPage();
		await screen.findByTestId('can-call-cards');
		expect(screen.getByRole('radio', { name: 'Cards view' })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		// Same tiles, same order, same deriveTileStatus → the same statuses.
		const cardStatuses = screen
			.getAllByTestId('api-card')
			.map((el) => el.getAttribute('data-status'));
		expect(cardStatuses).toEqual(rowStatuses);
	});

	it('Enter on a focused card opens the access sheet', async () => {
		seedViewer(['org:admin']);
		window.localStorage.setItem(API_VIEW_STORAGE_KEY, 'cards');
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('can-call-cards');

		screen.getAllByTestId('api-card')[0]!.focus();
		await user.keyboard('{Enter}');
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
	});

	it('has no tree trunk in cards mode, and every cell is one height', async () => {
		seedViewer(['org:admin']);
		window.localStorage.setItem(API_VIEW_STORAGE_KEY, 'cards');
		const { container } = renderPage();
		const grid = await screen.findByTestId('can-call-cards');
		await settleAnimations(container);
		const cells = [
			...within(grid).getAllByTestId('api-card'),
			within(grid).getByTestId('card-add-api-tile'),
		];
		const heights = new Set(cells.map((c) => Math.round(c.getBoundingClientRect().height)));
		expect([...heights]).toEqual([92]);
		for (const li of grid.querySelectorAll(':scope > li')) {
			expect(getComputedStyle(li, '::before').content).toBe('none');
		}
		expect(screen.queryByTestId('tree-trunk')).toBeNull();
		// Flush with the agent card: equal left/right gutters.
		const card = screen.getByTestId('agent-card').getBoundingClientRect();
		const rects = cells.map((c) => c.getBoundingClientRect());
		expect(Math.min(...rects.map((r) => r.left))).toBeCloseTo(card.left, 0);
		expect(Math.max(...rects.map((r) => r.right))).toBeCloseTo(card.right, 0);
	});

	it('keeps the toggle in the agent card, so the trunk starts 3px under it', async () => {
		seedViewer(['org:admin']);
		const { container } = renderPage();
		const toolbar = await waitForToolbar();
		await settleAnimations(container);
		const card = screen.getByTestId('agent-card');
		expect(card).toContainElement(toolbar);
		const firstElbow = screen.getAllByTestId('tree-elbow')[0]!;
		expect(
			Math.round(
				firstElbow.getBoundingClientRect().top - card.getBoundingClientRect().bottom,
			),
		).toBe(3);
	});

	it('clicking a card opens the access sheet', async () => {
		seedViewer(['org:admin']);
		window.localStorage.setItem(API_VIEW_STORAGE_KEY, 'cards');
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('can-call-cards');

		const [firstCard] = screen.getAllByTestId('api-card');
		await user.click(firstCard!);
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
	});

	it('the cards grid has a working add-API tile for a viewer who can bind', async () => {
		seedViewer(['org:admin']);
		window.localStorage.setItem(API_VIEW_STORAGE_KEY, 'cards');
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('can-call-cards');

		const addTile = screen.getByTestId('card-add-api-tile');
		expect(addTile).toBeEnabled();
		await user.click(addTile);
		// Opens the Add-APIs tray (the same flow the tree's Add APIs opens).
		expect(await screen.findByRole('dialog', { name: /Add APIs/i })).toBeInTheDocument();
	});

	it('disables the add-API tile with a reason when the viewer cannot bind', async () => {
		// A reader without agents:write can't add APIs — the tile is disabled and
		// its wrapper carries the reason on hover/focus (the Tooltip pattern the
		// tree's add button uses).
		seedViewer(['agents:read']);
		window.localStorage.setItem(API_VIEW_STORAGE_KEY, 'cards');
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('can-call-cards');

		const addTile = screen.getByTestId('card-add-api-tile');
		expect(addTile).toBeDisabled();
		// The reason rides on the focusable Tooltip wrapper around the disabled
		// button — reveal it by hovering the wrapper.
		await user.hover(addTile.parentElement!);
		expect(await screen.findByRole('tooltip')).toHaveTextContent(
			'Adding APIs needs permission to manage agents.',
		);
	});
});

describe('Can call — Expand all ⇄ Collapse all', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		resetAgentsStore();
		resetCredentialsStore();
		window.localStorage.clear();
	});
	afterEach(() => {
		setToken(null);
		window.localStorage.clear();
	});

	const toggle = () => screen.getByTestId('expand-all-rows');
	const tiles = () => screen.getAllByTestId('api-tile');
	const allPinned = () => tiles().every((t) => t.hasAttribute('data-pinned'));
	const nonePinned = () => tiles().every((t) => !t.hasAttribute('data-pinned'));

	it('reads Expand all with no pins: no tooltip, aria wired to the list, between Add API and the lens', async () => {
		seedViewer(['org:admin']);
		const { container } = renderPage();
		const toolbar = await waitForToolbar();
		expect(toggle()).toHaveTextContent('Expand all');
		expect(toggle()).toHaveAccessibleName('Expand all rows');
		expect(toggle()).not.toHaveAttribute('title');
		const list = document.getElementById(toggle().getAttribute('aria-controls')!);
		expect(list).toContainElement(tiles()[0]!);
		// … Add API, [Expand all], [list|cards].
		const radios = within(toolbar).getByRole('radiogroup');
		expect(
			toggle().compareDocumentPosition(radios) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		// Resting past a tooltip's hover delay opens nothing.
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		try {
			fireEvent.mouseEnter(toggle());
			act(() => void vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 200));
			expect(screen.queryByRole('tooltip')).toBeNull();
		} finally {
			vi.useRealTimers();
		}
		await settleAnimations(container);
		await checkA11y(container);
	});

	it('Expand all pins every row and turns into Collapse all, keeping its width', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		renderPage();
		await waitForToolbar();
		const width = toggle().getBoundingClientRect().width;

		await user.click(toggle());
		expect(toggle()).toHaveAccessibleName('Collapse all rows');
		await waitFor(() => expect(allPinned()).toBe(true));
		expect(toggle().getBoundingClientRect().width).toBe(width);

		await user.click(toggle());
		expect(toggle()).toHaveAccessibleName('Expand all rows');
		expect(nonePinned()).toBe(true);
	});

	it('with one row pinned by hand it reads Collapse all, and clears every pin', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		renderPage();
		await waitForToolbar();
		await user.click(within(tiles()[0]!).getByTestId('row-toggle'));
		expect(toggle()).toHaveAccessibleName('Collapse all rows');
		await user.click(toggle());
		expect(nonePinned()).toBe(true);
		expect(toggle()).toHaveAccessibleName('Expand all rows');
	});

	it('Escape after Expand all closes the latest pin first', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		renderPage();
		await waitForToolbar();
		await user.click(toggle());
		await waitFor(() => expect(allPinned()).toBe(true));
		const last = tiles()[tiles().length - 1]!;
		await user.keyboard('{Escape}');
		expect(last).not.toHaveAttribute('data-pinned');
		expect(tiles()[0]!).toHaveAttribute('data-pinned', 'true');
	});

	it('is not in cards view; switching to cards and back clears the pins', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		renderPage();
		const toolbar = await waitForToolbar();
		await user.click(toggle());
		await waitFor(() => expect(allPinned()).toBe(true));

		await user.click(within(toolbar).getByRole('radio', { name: 'Cards view' }));
		await screen.findByTestId('can-call-cards');
		expect(screen.queryByTestId('expand-all-rows')).toBeNull();

		await user.click(within(toolbar).getByRole('radio', { name: 'List view' }));
		await waitFor(() => expect(tiles().length).toBeGreaterThan(0));
		expect(nonePinned()).toBe(true);
		expect(toggle()).toHaveAccessibleName('Expand all rows');
	});

	it('switching agent clears the pins', async () => {
		seedViewer(['org:admin']);
		const user = userEvent.setup();
		renderPage();
		await waitForToolbar();
		await user.click(toggle());
		await waitFor(() => expect(allPinned()).toBe(true));

		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		await waitFor(() =>
			expect(screen.getByRole('tab', { name: /legacy-scraper/ })).toHaveAttribute(
				'aria-selected',
				'true',
			),
		);
		await user.click(screen.getByRole('tab', { name: /support-agent/ }));
		await waitForToolbar();
		// Back on the first agent before its card handed off: the card still
		// remounts once the strip's tab has landed, so the pins clear.
		await waitFor(() => expect(tiles().length > 0 && nonePinned()).toBe(true));
		expect(toggle()).toHaveAccessibleName('Expand all rows');
	});
});
