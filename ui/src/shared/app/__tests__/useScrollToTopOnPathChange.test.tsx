import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { Link, Route, Routes, useNavigate, useSearchParams } from 'react-router';
import { useScrollToTopOnPathChange } from '@/shared/app/useScrollToTopOnPathChange';

function Shell() {
	useScrollToTopOnPathChange();
	const [, setParams] = useSearchParams();
	const navigate = useNavigate();
	return (
		<>
			<Link to="/b">to b</Link>
			<Link to="/a#section">to a hash</Link>
			<button type="button" onClick={() => setParams({ show: 'jobs' })}>
				set param
			</button>
			<button type="button" onClick={() => navigate(-1)}>
				back
			</button>
			<Routes>
				<Route path="/a" element={<p>page a</p>} />
				<Route path="/b" element={<p>page b</p>} />
			</Routes>
		</>
	);
}

describe('useScrollToTopOnPathChange', () => {
	let scrollTo: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
	});
	afterEach(() => {
		scrollTo.mockRestore();
	});

	it('scrolls to the top on a pathname change', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Shell />, { route: '/a' });
		expect(scrollTo).not.toHaveBeenCalled();
		await user.click(screen.getByRole('link', { name: 'to b' }));
		expect(await screen.findByText('page b')).toBeInTheDocument();
		expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0 });
	});

	it('leaves the offset alone for query-param-only changes', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Shell />, { route: '/a' });
		await user.click(screen.getByRole('button', { name: 'set param' }));
		expect(scrollTo).not.toHaveBeenCalled();
	});

	it('skips browser Back (POP) and hash navigations', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Shell />, { route: '/a' });
		await user.click(screen.getByRole('link', { name: 'to b' }));
		scrollTo.mockClear();
		await user.click(screen.getByRole('button', { name: 'back' }));
		expect(await screen.findByText('page a')).toBeInTheDocument();
		expect(scrollTo).not.toHaveBeenCalled();

		await user.click(screen.getByRole('link', { name: 'to b' }));
		scrollTo.mockClear();
		await user.click(screen.getByRole('link', { name: 'to a hash' }));
		expect(await screen.findByText('page a')).toBeInTheDocument();
		expect(scrollTo).not.toHaveBeenCalled();
	});
});
