import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

/**
 * Bottom padding for pages that mount a `FooterActionBar`. Append it to the
 * page container (e.g. `<PageShell className={FOOTER_ACTION_BAR_PAGE_PADDING}>`)
 * so the last row of content scrolls clear of the fixed bar — and, below `md`,
 * of the bottom nav stacked beneath it.
 */
export const FOOTER_ACTION_BAR_PAGE_PADDING = 'pb-36 md:pb-24';

export interface FooterActionBarProps {
	/** Bar content — typically a row of `Button`s; the bar lays them out in a flex row. */
	children: ReactNode;
	/**
	 * Floating pill variant: a centred, rounded, shadowed dock hovering above
	 * the bottom edge instead of the default full-width edge-to-edge bar.
	 */
	floating?: boolean;
	/**
	 * Centre the floating pill on the bar's in-flow DOM parent (the content
	 * column it is mounted in) instead of the viewport. The app shell docks a
	 * collapsible rail beside `<main>` at `xl+`, so a viewport-centred pill
	 * sits visually off-centre relative to the page content whenever the rail
	 * is open — and the rail's width changes at runtime, so the offset can't
	 * be a static class. Opt-in so consumers whose parent already spans the
	 * viewport keep the measurement-free default. Only meaningful with
	 * `floating`.
	 */
	anchorToContainer?: boolean;
	className?: string;
}

/**
 * Fixed bottom action bar — the app's fixed-bottom primitive for page-level
 * actions, built to coexist with the mobile `BottomNavbar`:
 *
 * - Below `md` it sits ABOVE the bottom nav (which is `h-16` plus the
 *   safe-area inset, at `z-50`) — never under or over it. At `md` and up,
 *   where the nav is hidden, it sits at the true bottom of the viewport and
 *   owns the `env(safe-area-inset-bottom)` padding itself.
 * - Its z-index stays below the nav's `z-50`, so nav overlays (the "More"
 *   sheet) always win the stack.
 *
 * Presentational only — children carry the semantics (use `Button` for
 * actions). Pages that mount one must pad their own bottom content with
 * `FOOTER_ACTION_BAR_PAGE_PADDING` so the last row remains reachable.
 */
export function FooterActionBar({
	children,
	floating = false,
	anchorToContainer = false,
	className,
}: FooterActionBarProps) {
	const barRef = useRef<HTMLDivElement>(null);
	// Horizontal centre (viewport px) of the bar's DOM parent, when anchored.
	// `null` until the first measurement — the `left-1/2` class is the
	// viewport-centred fallback for that first paint.
	const [anchorCenter, setAnchorCenter] = useState<number | null>(null);

	useLayoutEffect(() => {
		if (!anchorToContainer) return undefined;
		// The bar is `fixed` (out of flow), so its DOM parent — the container
		// the consumer mounted it in — is the column to centre on.
		const parent = barRef.current?.parentElement;
		if (!parent) return undefined;
		const update = () => {
			const rect = parent.getBoundingClientRect();
			setAnchorCenter(rect.left + rect.width / 2);
		};
		update();
		// The parent's width tracks the app shell (rail collapse/expand); its
		// left edge can also move without a resize of its own (e.g. `mx-auto`
		// containers on viewport resize), so listen to both.
		const observer = new ResizeObserver(update);
		observer.observe(parent);
		window.addEventListener('resize', update);
		return () => {
			observer.disconnect();
			window.removeEventListener('resize', update);
		};
	}, [anchorToContainer]);

	return (
		<div
			ref={barRef}
			className={cn(
				'fixed z-40 flex items-center gap-2',
				// The bottom nav is `md:hidden`, so below `md` the bar clears the
				// nav's full height (4rem row + safe-area spacer); from `md` up
				// the bar owns the bottom edge and the safe area itself.
				floating
					? 'border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 shadow-card bottom-[calc(4rem+env(safe-area-inset-bottom)+0.75rem)] left-1/2 w-fit max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-full border px-3 py-2 backdrop-blur md:bottom-[calc(env(safe-area-inset-bottom)+0.75rem)]'
					: 'border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 px-page-gutter inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] border-t py-3 backdrop-blur md:bottom-0 md:pb-[calc(0.75rem+env(safe-area-inset-bottom))]',
				className,
			)}
			// Inline `left` wins over the `left-1/2` class; `-translate-x-1/2`
			// still centres the pill on that x.
			style={anchorToContainer && anchorCenter !== null ? { left: anchorCenter } : undefined}
		>
			{children}
		</div>
	);
}
