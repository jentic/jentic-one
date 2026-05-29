import { http, HttpResponse } from 'msw';
import { screen, renderWithProviders, userEvent, waitFor } from '../test-utils';
import { worker } from '../mocks/browser';
import DiscoverPage from '@/pages/DiscoverPage';

function renderDiscover(route = '/discover') {
	return renderWithProviders(<DiscoverPage />, { route, path: '/discover' });
}

describe('DiscoverPage (shell)', () => {
	it('renders the Discover page header and mounts the discovery view', async () => {
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));
		renderDiscover();

		expect(await screen.findByRole('heading', { name: /discover/i })).toBeInTheDocument();
		expect(screen.getByTestId('discover-toolbar')).toBeInTheDocument();
	});

	it('uses /apis?source=catalog so workspace items never bleed into the directory view', async () => {
		const seenSources: (string | null)[] = [];
		worker.use(
			http.get('/apis', ({ request }) => {
				const url = new URL(request.url);
				seenSources.push(url.searchParams.get('source'));
				return HttpResponse.json({
					data: [{ id: 'github.com', name: 'github.com', source: 'catalog' }],
					total: 1,
					page: 1,
				});
			}),
		);

		renderDiscover();
		await screen.findByText('github.com');
		expect(seenSources.every((s) => s === 'catalog')).toBe(true);
	});

	it('exposes the page-help dialog via the "?" key', async () => {
		const user = userEvent.setup();
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));
		renderDiscover();
		await screen.findByRole('heading', { name: /discover/i });

		(document.activeElement as HTMLElement | null)?.blur?.();
		await user.keyboard('?');
		expect(await screen.findByTestId('page-help-shortcuts')).toBeInTheDocument();
	});

	it('keeps the discovery toolbar mounted when the directory is empty', async () => {
		worker.use(http.get('/apis', () => HttpResponse.json({ data: [], total: 0, page: 1 })));
		renderDiscover();
		await waitFor(() => {
			expect(screen.getByTestId('discover-toolbar')).toBeInTheDocument();
		});
	});
});
