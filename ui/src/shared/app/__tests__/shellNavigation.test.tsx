import { useRef } from 'react';
import { describe, expect, it } from 'vitest';
import { useLocation, useNavigate } from 'react-router';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { useShellNavigation } from '@/shared/app/shellNavigation';
import { SHELL_SCROLL_ID } from '@/shared/lib/shellScroll';

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
				<button onClick={() => void navigate('/c#section')}>Go to C section</button>
				<button onClick={() => void navigate('/b#missing')}>Go to B missing anchor</button>
				<button onClick={() => void navigate(-1)}>Back</button>
			</nav>
			<main
				ref={mainRef}
				id={SHELL_SCROLL_ID}
				tabIndex={-1}
				style={{ height: 300, overflowY: 'auto' }}
			>
				<h1>
					{pathname}
					{search}
				</h1>
				<div style={{ height: 3000 }}>
					{pathname === '/c' && (
						<h2 id="section" style={{ marginTop: 2000 }}>
							Section
						</h2>
					)}
				</div>
			</main>
		</>
	);
}

/** The shell's scroller, `#app-scroll` — what the shell scrolls, not the window. */
function main(): HTMLElement {
	const el = document.getElementById(SHELL_SCROLL_ID);
	if (!el) throw new Error(`#${SHELL_SCROLL_ID} not rendered`);
	return el;
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

	it('opens a new page at its #hash anchor instead of the top', async () => {
		await page.viewport(1024, 800);
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();
		await scrollMainTo(300);

		await user.click(screen.getByRole('button', { name: 'Go to C section' }));
		await screen.findByRole('heading', { name: '/c' });
		const section = screen.getByRole('heading', { name: 'Section' });
		await waitFor(() =>
			expect(
				Math.round(
					section.getBoundingClientRect().top - main().getBoundingClientRect().top,
				),
			).toBe(0),
		);
		expect(main().scrollTop).toBeGreaterThan(1000);
	});

	it('falls back to the top when the #hash target is not rendered', async () => {
		await page.viewport(1024, 800);
		renderWithProviders(<Shell />, { route: '/a' });
		const user = userEvent.setup();
		await scrollMainTo(900);

		await user.click(screen.getByRole('button', { name: 'Go to B missing anchor' }));
		await screen.findByRole('heading', { name: '/b' });
		expect(main().scrollTop).toBe(0);
	});
});
