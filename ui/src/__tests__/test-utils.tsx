import type { ReactElement, ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router';
import { render, type RenderOptions } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

interface Options extends Omit<RenderOptions, 'wrapper'> {
	/** Initial router location. Defaults to '/'. */
	route?: string;
	/** When set, renders `ui` under a `<Route path={path}>` so `useParams` works. */
	path?: string;
}

/**
 * Render a component under the providers every page expects: a fresh
 * QueryClient (retries off so error states render immediately) and a
 * MemoryRouter. Returns the testing-library result plus the `queryClient`.
 *
 * MSW is started globally in src/__tests__/setup.ts; override per-test with
 * `worker.use(createErrorHandler(...))`.
 */
export function renderWithProviders(ui: ReactElement, options: Options = {}) {
	const { route = '/', path, ...renderOptions } = options;

	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});

	function Wrapper({ children }: { children: ReactNode }) {
		return (
			<QueryClientProvider client={queryClient}>
				<MemoryRouter initialEntries={[route]}>
					{path ? (
						<Routes>
							<Route path={path} element={children} />
						</Routes>
					) : (
						children
					)}
				</MemoryRouter>
			</QueryClientProvider>
		);
	}

	return { ...render(ui, { wrapper: Wrapper, ...renderOptions }), queryClient };
}

export * from '@testing-library/react';
export { default as userEvent } from '@testing-library/user-event';

/**
 * Run axe against a rendered container and assert no critical/serious a11y
 * violations. Uses axe-core directly (browser-mode compatible). Feature PRs
 * call this on every page-level test.
 *
 * When a modal overlay is open inside `container`, the audit narrows to the
 * topmost modal. An overlay test passes `document.body` because the sheet
 * portals out of the render container, and a `SheetPrimitive` backdrop
 * deliberately obscures the page behind it (`bg-black/50 backdrop-blur-sm`).
 * Compositing the page's text under that translucent, blurred scrim makes
 * axe's colour-contrast result indeterminate — the same text lands in
 * `violations` or `incomplete` depending on where in the 300ms backdrop
 * transition the audit happens to run. The modal is the surface under test in
 * those specs, and the page behind it gets its own no-overlay audit, so
 * scoping to the modal is both the deterministic and the meaningful check.
 */
export async function checkA11y(container: Element): Promise<void> {
	const { default: axe } = await import('axe-core');
	// Topmost = last in DOM order: each overlay portals to the end of <body>,
	// so a stacked sheet or a native dialog above a sheet audits itself.
	const modals = container.querySelectorAll<HTMLElement>('[aria-modal="true"], dialog[open]');
	const target = modals.length > 0 ? modals[modals.length - 1] : container;
	const results = await axe.run(target);
	const critical = results.violations.filter(
		(v) => v.impact === 'critical' || v.impact === 'serious',
	);
	if (critical.length > 0) {
		const summary = critical
			.map(
				(v) =>
					`${v.id}: ${v.help}\n  ${v.nodes.map((n) => n.target.join(' ')).join('\n  ')}`,
			)
			.join('\n');
		throw new Error(`axe found ${critical.length} critical/serious violation(s):\n${summary}`);
	}
}

/**
 * Factory for one-off MSW error/edge handlers, registered per-test via
 * `worker.use(createErrorHandler('get', '/apis', { status: 500 }))`.
 */
export function createErrorHandler(
	method: 'get' | 'post' | 'patch' | 'put' | 'delete',
	path: string,
	options: { status?: number; body?: unknown; networkError?: boolean } = {},
) {
	const { status = 500, body, networkError = false } = options;
	return http[method](path, () =>
		networkError
			? HttpResponse.error()
			: HttpResponse.json((body ?? { detail: 'Server error' }) as object, { status }),
	);
}
