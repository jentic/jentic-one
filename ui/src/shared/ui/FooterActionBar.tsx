import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

/** Bottom padding for pages that mount a `FooterActionBar`, so the last row of
 * content scrolls clear of the fixed bar and, below `md`, of the bottom nav. */
export const FOOTER_ACTION_BAR_PAGE_PADDING = 'pb-36 md:pb-24';

export interface FooterActionBarProps {
	/** Bar content — typically a row of `Button`s; the bar lays them out in a flex row. */
	children: ReactNode;
	/** Floating pill variant: a centred rounded dock above the bottom edge. */
	floating?: boolean;
	/** Centre the floating pill on the bar's DOM parent instead of the viewport:
	 * the shell's `xl+` rail changes width at runtime, so a viewport-centred pill
	 * sits off-centre and no static class can fix it. Only with `floating`. */
	anchorToContainer?: boolean;
	className?: string;
}

/**
 * Fixed bottom action bar, built to coexist with the mobile `BottomNavbar`: below
 * `md` it sits ABOVE the nav, and from `md` up it owns the bottom edge and the
 * safe-area padding. Pages that mount one must pad their bottom content with
 * `FOOTER_ACTION_BAR_PAGE_PADDING`.
 */
export function FooterActionBar({
	children,
	floating = false,
	anchorToContainer = false,
	className,
}: FooterActionBarProps) {
	const barRef = useRef<HTMLDivElement>(null);
	// Horizontal centre (viewport px) of the bar's DOM parent, when anchored.
	// `null` until measured — `left-1/2` is the fallback for that first paint.
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
		// The parent's width tracks the rail collapse/expand, and its left edge can
		// move without a resize of its own, so listen to both.
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
				// The nav is `md:hidden`, so below `md` the bar clears its full height;
				// from `md` up the bar owns the bottom edge and the safe area.
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
