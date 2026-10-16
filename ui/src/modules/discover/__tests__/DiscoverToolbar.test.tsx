import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { act, renderWithProviders, screen } from '@/__tests__/test-utils';
import { DiscoverToolbar } from '@/modules/discover/components/DiscoverToolbar';
import { SHELL_SCROLL_ID } from '@/shared/lib/shellScroll';

/**
 * A controllable IntersectionObserver. The real one reports on the browser's
 * rendering schedule, which a loaded headless runner can stall; this one
 * reports only when `reportFromLayout` asks it to, so every assertion follows
 * a delivered entry instead of racing one.
 */
class FakeObserver {
	static instances: FakeObserver[] = [];
	readonly callback: IntersectionObserverCallback;
	readonly options: IntersectionObserverInit | undefined;
	readonly targets = new Set<Element>();
	constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
		this.callback = callback;
		this.options = options;
		FakeObserver.instances.push(this);
	}
	observe(el: Element) {
		this.targets.add(el);
	}
	unobserve(el: Element) {
		this.targets.delete(el);
	}
	disconnect() {
		this.targets.clear();
	}
	takeRecords() {
		return [];
	}
	root = null;
	rootMargin = '';
	thresholds = [];
}

/** The observer watching the toolbar's sentinel. */
function sentinelObserver(): FakeObserver {
	const sentinel = screen.getByTestId('discover-toolbar-sentinel');
	const obs = FakeObserver.instances.find((o) => o.targets.has(sentinel));
	if (!obs) throw new Error('no observer is watching the toolbar sentinel');
	return obs;
}

/**
 * Deliver the entry a real observer would report for the sentinel's current
 * layout. Geometry is read synchronously (it forces layout), against the root
 * the component chose — so a viewport-rooted observer still gets the pin
 * point wrong by the navbar offset, as it would in the app.
 */
function reportFromLayout() {
	const obs = sentinelObserver();
	const sentinel = screen.getByTestId('discover-toolbar-sentinel');
	const root = obs.options?.root;
	const rootBounds =
		root instanceof Element
			? root.getBoundingClientRect()
			: new DOMRectReadOnly(0, 0, window.innerWidth, window.innerHeight);
	const rect = sentinel.getBoundingClientRect();
	const isIntersecting = rect.bottom > rootBounds.top && rect.top < rootBounds.bottom;
	// The part of the sentinel inside the root; empty when it isn't intersecting.
	const intersectionRect = isIntersecting
		? DOMRectReadOnly.fromRect({
				x: rect.left,
				y: Math.max(rect.top, rootBounds.top),
				width: rect.width,
				height:
					Math.min(rect.bottom, rootBounds.bottom) - Math.max(rect.top, rootBounds.top),
			})
		: new DOMRectReadOnly(0, 0, 0, 0);
	const entry = {
		target: sentinel,
		isIntersecting,
		intersectionRatio: isIntersecting ? 1 : 0,
		boundingClientRect: rect,
		rootBounds,
		intersectionRect,
		time: performance.now(),
	} as IntersectionObserverEntry;
	act(() => {
		obs.callback([entry], obs as unknown as IntersectionObserver);
	});
}

/** The shell's scroller, offset below a stand-in for the fixed navbar — so a
 * viewport-rooted observer would get the pin point wrong by that offset. */
function Shell({ children, headerHeight }: { children: React.ReactNode; headerHeight: number }) {
	return (
		<main
			id={SHELL_SCROLL_ID}
			style={{
				position: 'fixed',
				top: 48,
				left: 0,
				right: 0,
				height: 400,
				overflowY: 'auto',
			}}
		>
			<div style={{ height: headerHeight }}>Page header</div>
			{children}
			<div style={{ height: 3000 }}>Catalog</div>
		</main>
	);
}

function renderToolbar(headerHeight = 120) {
	renderWithProviders(
		<Shell headerHeight={headerHeight}>
			<DiscoverToolbar
				query=""
				onQueryChange={vi.fn()}
				filter="all"
				onFilterChange={vi.fn()}
				onRefresh={vi.fn()}
			/>
		</Shell>,
	);
	return {
		toolbar: screen.getByTestId('discover-toolbar'),
		scroller: document.getElementById(SHELL_SCROLL_ID)!,
	};
}

describe('DiscoverToolbar', () => {
	beforeEach(() => {
		FakeObserver.instances = [];
		vi.stubGlobal('IntersectionObserver', FakeObserver);
	});
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('observes its sentinel against the shell scroller, not the viewport', () => {
		const { scroller } = renderToolbar();
		expect(sentinelObserver().options?.root).toBe(scroller);
	});

	it('flags data-scrolled only while the sticky bar is pinned to the scroller top', () => {
		const { toolbar, scroller } = renderToolbar();
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');

		// Scrolled, but not yet up to the bar: still at rest.
		scroller.scrollTop = 60;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');

		// Just past the page header: the bar pins and gets its hairline. The
		// sentinel is still below the viewport's top here — only the scroller's
		// edge (under the navbar) says it has left.
		scroller.scrollTop = 150;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'true');
		expect(Math.round(toolbar.getBoundingClientRect().top)).toBe(48);

		// Well past it: still pinned.
		scroller.scrollTop = 600;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'true');
		expect(Math.round(toolbar.getBoundingClientRect().top)).toBe(48);

		// Back to the top: it lets go.
		scroller.scrollTop = 0;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');
	});

	it('stays at rest while the bar is below the fold', () => {
		// A header taller than the scroller: at the top the sentinel sits out of
		// view beneath it — "not intersecting", but not pinned either.
		const { toolbar, scroller } = renderToolbar(1000);
		// The entry a real observer delivers on observe(), at mount.
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');

		scroller.scrollTop = 1200;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'true');

		scroller.scrollTop = 0;
		reportFromLayout();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');
	});

	it('disconnects its observer on unmount', () => {
		const { unmount } = renderWithProviders(
			<DiscoverToolbar
				query=""
				onQueryChange={vi.fn()}
				filter="all"
				onFilterChange={vi.fn()}
				onRefresh={vi.fn()}
			/>,
		);
		const obs = sentinelObserver();
		unmount();
		expect(obs.targets.size).toBe(0);
	});
});
