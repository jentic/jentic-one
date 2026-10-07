import { afterEach } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router';
import { render } from '@testing-library/react';
import { renderWithProviders, screen, checkA11y, userEvent } from '@/__tests__/test-utils';
import { BackButton } from '@/shared/ui/BackButton';

/** A two-entry history (/from → /detail) with the button on /detail. */
function renderWithHistory() {
	return render(
		<MemoryRouter initialEntries={['/from', '/detail']} initialIndex={1}>
			<Routes>
				<Route path="/from" element={<div data-testid="from-page" />} />
				<Route path="/apis" element={<div data-testid="fallback-page" />} />
				<Route path="/detail" element={<BackButton to="/apis" label="Back" />} />
			</Routes>
		</MemoryRouter>,
	);
}

describe('BackButton', () => {
	const initialState: unknown = window.history.state;
	afterEach(() => {
		window.history.replaceState(initialState, '');
	});

	it('renders a history-aware button by default', () => {
		renderWithProviders(<BackButton to="/apis" label="Back to APIs" />);
		const btn = screen.getByTestId('back-button');
		expect(btn.tagName).toBe('BUTTON');
		expect(btn).toHaveTextContent('Back to APIs');
	});

	it('renders a link when useHistory is false', () => {
		renderWithProviders(<BackButton to="/apis" label="Back to APIs" useHistory={false} />);
		const link = screen.getByTestId('back-button');
		expect(link.tagName).toBe('A');
		expect(link).toHaveAttribute('href', '/apis');
	});

	it('steps back when there is an in-app entry behind', async () => {
		const user = userEvent.setup();
		renderWithHistory();
		await user.click(screen.getByTestId('back-button'));
		expect(await screen.findByTestId('from-page')).toBeInTheDocument();
	});

	it('falls back to `to` on the first entry (direct visit)', async () => {
		const user = userEvent.setup();
		render(
			<MemoryRouter initialEntries={['/detail']}>
				<Routes>
					<Route path="/apis" element={<div data-testid="fallback-page" />} />
					<Route path="/detail" element={<BackButton to="/apis" label="Back" />} />
				</Routes>
			</MemoryRouter>,
		);
		await user.click(screen.getByTestId('back-button'));
		expect(await screen.findByTestId('fallback-page')).toBeInTheDocument();
	});

	it('trusts the browser router’s entry index when present (idx 0 → fallback)', async () => {
		// BrowserRouter keeps idx 0 across replace navigations on the tab's
		// first app entry; that wins over a non-initial location key.
		window.history.replaceState({ idx: 0 }, '');
		const user = userEvent.setup();
		renderWithHistory();
		await user.click(screen.getByTestId('back-button'));
		expect(await screen.findByTestId('fallback-page')).toBeInTheDocument();
	});

	it('trusts the browser router’s entry index when present (idx > 0 → back)', async () => {
		window.history.replaceState({ idx: 3 }, '');
		const user = userEvent.setup();
		renderWithHistory();
		await user.click(screen.getByTestId('back-button'));
		expect(await screen.findByTestId('from-page')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<BackButton to="/apis" label="Back to APIs" />);
		await checkA11y(container);
	});
});
