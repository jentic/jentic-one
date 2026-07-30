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
 */
export async function checkA11y(container: Element): Promise<void> {
	const { default: axe } = await import('axe-core');
	const results = await axe.run(container);
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
