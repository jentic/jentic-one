import type { ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { render, type RenderOptions } from '@testing-library/react';
import { MotionConfig } from 'framer-motion';
import { http, HttpResponse } from 'msw';

interface Options extends Omit<RenderOptions, 'wrapper'> {
	route?: string;
	path?: string;
}

export function renderWithProviders(ui: ReactElement, options: Options = {}) {
	const { route = '/', path, ...renderOptions } = options;

	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 0 },
			mutations: { retry: false },
		},
	});

	function Wrapper({ children }: { children: React.ReactNode }) {
		return (
			// `reducedMotion="always"` mirrors the production `MotionConfig` in
			// `<App />`, but in framer-motion v12 `reducedMotion` only skips
			// *transform* animations — `opacity` keeps tweening because it is
			// considered an essential UX cue. That's a problem for axe
			// colour-contrast checks: they fire at frame 0 while a `motion.div`
			// is still at `opacity: 0` and observe a translucent `bg-primary`
			// button as ~1:1 contrast against the page background.
			//
			// Forcing `transition.duration = 0` collapses every animation to
			// its final state on the very first paint, regardless of which
			// property is being animated.
			<MotionConfig reducedMotion="always" transition={{ duration: 0 }}>
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
			</MotionConfig>
		);
	}

	return { ...render(ui, { wrapper: Wrapper, ...renderOptions }), queryClient };
}

export * from '@testing-library/react';
export { default as userEvent } from '@testing-library/user-event';

// ---------------------------------------------------------------------------
// MSW error handler factory
// ---------------------------------------------------------------------------

export function createErrorHandler(
	method: 'get' | 'post' | 'patch' | 'put' | 'delete',
	path: string,
	options: { status?: number; body?: unknown; networkError?: boolean } = {},
) {
	const { status = 500, body, networkError = false } = options;
	return http[method](path, () =>
		networkError
			? HttpResponse.error()
			: HttpResponse.json(body ?? { detail: 'Server error' }, { status }),
	);
}
