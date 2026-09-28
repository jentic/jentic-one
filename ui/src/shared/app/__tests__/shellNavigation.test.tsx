import { useRef } from 'react';
import { describe, expect, it } from 'vitest';
import { useLocation, useNavigate } from 'react-router';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { useShellNavigation } from '@/shared/app/shellNavigation';

/** A shell-like scroller whose pages are tall enough to scroll. */
function Shell() {
	const mainRef = useRef<HTMLElement>(null);
	useShellNavigation(mainRef);
	const navigate = useNavigate();
	const { pathname, search } = useLocation();
	return (
		<>
			<nav>
				<button onClick={() => void navigate('/b')}>Go to B</button>
				<button onClick={() => void navigate('/a?filter=x')}>Filter A</button>
				<button onClick={() => void navigate(-1)}>Back</button>
			</nav>
			<main
				ref={mainRef}
				data-testid="main"
				tabIndex={-1}
				style={{ height: 300, overflowY: 'auto' }}
			>
				<h1>
					{pathname}
					{search}
				</h1>
				<div style={{ height: 3000 }} />
			</main>
		</>
	);
}

function main(): HTMLElement {
	return screen.getByTestId('main');
}

async function scrollMainTo(top: number): Promise<void> {
	main().scrollTop = top;
	// The position is recorded from the scroll event, which fires asynchronously.
	await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));
}

describe('useShellNavigation', () => {
	it('starts a new page at its top and restores the old one on Back', async () => {
		await page.viewport(1024, 800);
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();
		await scrollMainTo(900);

		await user.click(screen.getByRole('button', { name: 'Go to B' }));
		await screen.findByRole('heading', { name: '/b' });
		expect(main().scrollTop).toBe(0);

		await user.click(screen.getByRole('button', { name: 'Back' }));
		await screen.findByRole('heading', { name: '/a' });
		await waitFor(() => expect(main().scrollTop).toBe(900));
	});

	it('keeps its place when only the query string changes', async () => {
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();
		await scrollMainTo(700);

		await user.click(screen.getByRole('button', { name: 'Filter A' }));
		await screen.findByRole('heading', { name: '/a?filter=x' });
		expect(main().scrollTop).toBe(700);
	});

	it('restores an entry created by a query-only change', async () => {
		await page.viewport(1024, 800);
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();
		await scrollMainTo(700);

		await user.click(screen.getByRole('button', { name: 'Filter A' }));
		await screen.findByRole('heading', { name: '/a?filter=x' });
		await user.click(screen.getByRole('button', { name: 'Go to B' }));
		await screen.findByRole('heading', { name: '/b' });
		expect(main().scrollTop).toBe(0);

		await user.click(screen.getByRole('button', { name: 'Back' }));
		await screen.findByRole('heading', { name: '/a?filter=x' });
		await waitFor(() => expect(main().scrollTop).toBe(700));
	});

	it('hands keyboard focus to the scroller on navigation', async () => {
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();

		await user.click(screen.getByRole('button', { name: 'Go to B' }));
		await screen.findByRole('heading', { name: '/b' });
		expect(main()).toHaveFocus();
	});
});
