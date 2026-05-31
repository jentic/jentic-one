import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { DiscoverRedirect } from '@/App';

/**
 * Tiny landing element that renders the current `pathname + search`
 * — lets us assert what the router redirected to without relying on
 * the real Discover page being mountable in test (it pulls in the
 * full DiscoveryView with MSW fixtures, etc).
 */
function Landed() {
	const { pathname, search } = useLocation();
	return (
		<div>
			<span data-testid="landed-pathname">{pathname}</span>
			<span data-testid="landed-search">{search}</span>
		</div>
	);
}

function renderWithRoute(initial: string) {
	return render(
		<MemoryRouter initialEntries={[initial]}>
			<Routes>
				<Route path="/search" element={<DiscoverRedirect />} />
				<Route path="/catalog" element={<DiscoverRedirect />} />
				<Route path="/discover" element={<Landed />} />
			</Routes>
		</MemoryRouter>,
	);
}

describe('App route redirects', () => {
	it('/catalog → /discover with no query string', () => {
		renderWithRoute('/catalog');
		expect(screen.getByTestId('landed-pathname').textContent).toBe('/discover');
		expect(screen.getByTestId('landed-search').textContent).toBe('');
	});

	it('/catalog?q=stripe → /discover?q=stripe (query string preserved)', () => {
		renderWithRoute('/catalog?q=stripe');
		expect(screen.getByTestId('landed-pathname').textContent).toBe('/discover');
		expect(screen.getByTestId('landed-search').textContent).toBe('?q=stripe');
	});

	it('/search?q=stripe → /discover?q=stripe', () => {
		renderWithRoute('/search?q=stripe');
		expect(screen.getByTestId('landed-pathname').textContent).toBe('/discover');
		expect(screen.getByTestId('landed-search').textContent).toBe('?q=stripe');
	});

	it('preserves multiple params (q, type, source, inspect, op)', () => {
		renderWithRoute(
			'/catalog?q=github&type=workflow&source=directory&inspect=github.com&op=GET%20/repos',
		);
		expect(screen.getByTestId('landed-pathname').textContent).toBe('/discover');
		expect(screen.getByTestId('landed-search').textContent).toBe(
			'?q=github&type=workflow&source=directory&inspect=github.com&op=GET%20/repos',
		);
	});
});
