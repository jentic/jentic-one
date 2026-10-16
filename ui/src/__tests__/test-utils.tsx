import type { ReactElement, ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router';
import { render, screen, waitFor, type RenderOptions } from '@testing-library/react';
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
 * Wait until every finite animation and transition touching `root` has
 * finished. axe reads colours from one frame, so a row still fading in from
 * opacity 0 (a framer-motion entrance, a sheet sliding in, a tick's colour
 * transition) reports a colour-contrast failure the settled UI doesn't have.
 * Call it right before `checkA11y` on a surface that animates. Infinite loops
 * (spinners, pulses) never end, so they are left out.
 */
export async function settleAnimations(root: Element = document.body): Promise<void> {
	await waitFor(
		() => {
			const moving = document.getAnimations().filter((animation) => {
				if (animation.playState !== 'running') return false;
				const target =
					animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
				if (!target || !root.contains(target)) return false;
				return animation.effect?.getComputedTiming().endTime !== Infinity;
			});
			if (moving.length > 0) {
				throw new Error(`${moving.length} animation(s) still running under the root`);
			}
		},
		{ timeout: 3000 },
	);
}

/**
 * Wait until the topmost sheet has finished entering, so axe measures contrast
 * against the settled surface. The backdrop's opacity fade only starts once the
 * sheet flips to `open` (a double rAF after mount), so `settleAnimations` alone
 * can pass before it has begun; reading the computed opacity can't. `items`
 * returns content that fades in on its own (a framer-motion stagger from
 * opacity 0): each must reach opacity 1, and an empty list fails rather than
 * passing vacuously. axe blends a translucent ancestor into the colours it
 * measures, so auditing a half-faded row reads its text as low-contrast.
 */
export async function sheetSettled(items?: () => Element[]): Promise<void> {
	await waitFor(
		() => {
			const backdrops = screen.getAllByTestId('sheet-backdrop');
			const backdrop = backdrops[backdrops.length - 1];
			if (getComputedStyle(backdrop).opacity !== '1') {
				throw new Error('the sheet backdrop is still fading in');
			}
			if (!items) return;
			const list = items();
			if (list.length === 0) throw new Error('no sheet items to wait on');
			const fading = list.filter((item) => getComputedStyle(item).opacity !== '1');
			if (fading.length > 0) {
				throw new Error(`${fading.length} sheet item(s) still fading in`);
			}
		},
		{ timeout: 3000 },
	);
}

/**
 * Run axe against a rendered container and assert no critical/serious violations.
 *
 * The caller declares the scope: an overlay spec passes `{ modal: true }` (usually
 * with `document.body`, since the sheet portals out) and gets the topmost modal
 * audited. Both directions throw rather than silently audit the other surface.
 */
export async function checkA11y(
	container: Element,
	options: { modal?: boolean } = {},
): Promise<void> {
	const { default: axe } = await import('axe-core');
	// Topmost = last in DOM order: each overlay portals to the end of <body>.
	// `:not([hidden])` skips a closed `keepMounted` sheet.
	const modals = container.querySelectorAll<HTMLElement>(
		'[aria-modal="true"]:not([hidden]), dialog[open]',
	);
	if (options.modal && modals.length === 0) {
		throw new Error(
			'checkA11y({ modal: true }) found no open modal in the container — ' +
				'the overlay under test never opened, so nothing was audited.',
		);
	}
	if (!options.modal && modals.length > 0) {
		throw new Error(
			`checkA11y found ${modals.length} open modal(s) in a page-level audit. ` +
				'Pass `{ modal: true }` if the overlay is the surface under test; ' +
				'otherwise close it and await its removal from the DOM (the exit ' +
				'transition keeps `aria-modal` in place for ~300ms) before auditing ' +
				'the page — the backdrop makes contrast results indeterminate.',
		);
	}
	const target = options.modal ? modals[modals.length - 1] : container;
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
 * Swap `window.localStorage` for an in-memory store until the returned restore
 * runs. Test files share one origin, so a real write reaches every other file
 * as a `storage` event; tests that write app-wide preferences (the theme) use
 * this so their writes stay inside the file. `failWrites` makes `setItem`
 * throw, as a browser with storage blocked does.
 */
export function stubLocalStorage(options: { failWrites?: boolean } = {}): () => void {
	const real = Object.getOwnPropertyDescriptor(window, 'localStorage');
	const data = new Map<string, string>();
	const memory: Storage = {
		get length() {
			return data.size;
		},
		clear: () => data.clear(),
		getItem: (key) => data.get(key) ?? null,
		key: (index) => [...data.keys()][index] ?? null,
		removeItem: (key) => void data.delete(key),
		setItem: (key, value) => {
			if (options.failWrites) throw new DOMException('Storage is blocked', 'SecurityError');
			data.set(key, String(value));
		},
	};
	Object.defineProperty(window, 'localStorage', { configurable: true, value: memory });
	return () => {
		if (real) Object.defineProperty(window, 'localStorage', real);
		else delete (window as { localStorage?: Storage }).localStorage;
	};
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
