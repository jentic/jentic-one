import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/shared/lib/utils';

/**
 * Shared dropdown / popover primitives for menus that hang off a trigger
 * element (User menu, "More" nav overflow, future row-action menus, etc.).
 *
 * Why this lives here: `NavTabs`, `UserMenu`, and the mobile `BottomNavbar`
 * each need the same "close on outside click + Escape + render an
 * absolutely-positioned panel with inset rounded items" behaviour. This
 * module owns that pattern in one place so the menu chrome stays visually
 * consistent (padding, dividers, hover rounding) wherever it's used.
 *
 * Public surface:
 *
 *  - {@link useDismissable} — wires outside-click + Escape close into a
 *    container ref. The trigger and the panel must both be inside the same
 *    container (so clicks on the trigger don't count as "outside").
 *  - {@link useViewportClamp} — nudges a trigger-anchored panel back inside
 *    the viewport when the trigger sits too close to a screen edge.
 *  - {@link MenuPanel} — the absolutely-positioned popover shell.
 *  - {@link AnchoredMenuPanel} — a portalled, fixed-position variant for
 *    triggers that live inside `overflow-hidden`/scroll ancestors (table
 *    cards, sheets) where {@link MenuPanel} would be clipped.
 *  - {@link MenuSeparator} — a thin inset divider for grouping items.
 *  - {@link menuItemClass} — the canonical item className (consume on
 *    `<AppLink role="menuitem">`, `<Button role="menuitem">`, etc.).
 */

/** Wires "click outside" + Escape into a container ref. */
export function useDismissable<T extends HTMLElement>(open: boolean, onClose: () => void) {
	const ref = useRef<T>(null);

	useEffect(() => {
		if (!open) return;
		function onMouseDown(e: MouseEvent) {
			if (ref.current && !ref.current.contains(e.target as Node)) {
				onClose();
			}
		}
		function onKey(e: KeyboardEvent) {
			if (e.key === 'Escape') onClose();
		}
		window.addEventListener('mousedown', onMouseDown);
		window.addEventListener('keydown', onKey);
		return () => {
			window.removeEventListener('mousedown', onMouseDown);
			window.removeEventListener('keydown', onKey);
		};
	}, [open, onClose]);

	return ref;
}

/**
 * Keeps a trigger-anchored panel inside the viewport horizontally.
 *
 * Panels anchor to their trigger with `absolute left-0`/`right-0`, which
 * overflows the screen edge when the trigger sits near (or wraps to) the
 * opposite side — e.g. a wide dropdown whose trigger lands on the left half
 * of a phone header. Attach the returned ref to the panel: after it opens
 * (and on resize) the panel is measured and nudged back into view with a
 * transform, preserving the anchor in the common case where nothing
 * overflows.
 */
export function useViewportClamp<T extends HTMLElement>(open: boolean, margin = 12) {
	const ref = useRef<T>(null);

	useLayoutEffect(() => {
		const el = ref.current;
		if (!open || !el) return;
		function place() {
			if (!el) return;
			el.style.transform = '';
			const rect = el.getBoundingClientRect();
			const shift =
				rect.left < margin
					? margin - rect.left
					: Math.min(0, window.innerWidth - margin - rect.right);
			if (shift !== 0) el.style.transform = `translateX(${shift}px)`;
		}
		place();
		window.addEventListener('resize', place);
		return () => window.removeEventListener('resize', place);
	}, [open, margin]);

	return ref;
}

export interface MenuPanelProps {
	children: ReactNode;
	/** Horizontal alignment of the panel relative to the trigger. */
	align?: 'left' | 'right';
	/** Extra classes appended to the default panel chrome. */
	className?: string;
}

/**
 * The absolutely-positioned popover shell. Renders the padded outer card
 * — items go inside as inset pills using {@link menuItemClass}.
 */
export function MenuPanel({ children, align = 'left', className }: MenuPanelProps) {
	return (
		<div
			role="menu"
			className={cn(
				'border-border bg-background absolute top-full z-50 mt-1.5 min-w-[180px] rounded-lg border p-1 shadow-lg',
				align === 'right' ? 'right-0' : 'left-0',
				className,
			)}
		>
			{children}
		</div>
	);
}

export interface AnchoredMenuPanelProps {
	children: ReactNode;
	/** The trigger element the panel hangs off (measured for placement). */
	anchorRef: RefObject<HTMLElement | null>;
	/** Called on outside click / Escape. Clicks on the anchor are "inside". */
	onClose: () => void;
	/** Horizontal alignment of the panel relative to the trigger. */
	align?: 'left' | 'right';
	/** Extra classes appended to the default panel chrome. */
	className?: string;
}

/**
 * Fixed-position sibling of {@link MenuPanel} that portals to `document.body`,
 * so it escapes `overflow-hidden` / scroll ancestors (Card-wrapped tables,
 * sheet bodies) that would clip an absolutely-positioned panel. Placement
 * anchors to the trigger's rect, flips above it when the viewport bottom is
 * too close, and tracks resize/ancestor scroll.
 *
 * Unlike `MenuPanel` + `useDismissable` (which require trigger and panel in
 * one container), the portal breaks DOM containment, so this component owns
 * dismissal itself: clicks inside the panel or on the anchor never dismiss —
 * the trigger's own toggle handles the latter.
 */
export function AnchoredMenuPanel({
	children,
	anchorRef,
	onClose,
	align = 'left',
	className,
}: AnchoredMenuPanelProps) {
	const panelRef = useRef<HTMLDivElement>(null);
	// First paint happens off-screen so the panel can be measured before the
	// flip-above-vs-below decision — avoids a visible reposition jump.
	const [pos, setPos] = useState<CSSProperties>({ top: -9999, left: -9999 });

	useLayoutEffect(() => {
		function place() {
			const anchor = anchorRef.current;
			const panel = panelRef.current;
			if (!anchor || !panel) return;
			const rect = anchor.getBoundingClientRect();
			const height = panel.offsetHeight;
			const below = rect.bottom + 6;
			const fitsBelow = below + height <= window.innerHeight - 8;
			const top = !fitsBelow && rect.top - height - 6 >= 8 ? rect.top - height - 6 : below;
			setPos(
				align === 'right'
					? { top, right: Math.max(8, window.innerWidth - rect.right), left: 'auto' }
					: { top, left: Math.max(8, rect.left), right: 'auto' },
			);
		}
		place();
		window.addEventListener('resize', place);
		// Capture-phase so scrolls inside nested scroll containers reposition too.
		window.addEventListener('scroll', place, true);
		return () => {
			window.removeEventListener('resize', place);
			window.removeEventListener('scroll', place, true);
		};
	}, [anchorRef, align]);

	useEffect(() => {
		function onMouseDown(e: MouseEvent) {
			const target = e.target as Node;
			if (panelRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
			onClose();
		}
		function onKey(e: KeyboardEvent) {
			if (e.key === 'Escape') onClose();
		}
		window.addEventListener('mousedown', onMouseDown);
		window.addEventListener('keydown', onKey);
		return () => {
			window.removeEventListener('mousedown', onMouseDown);
			window.removeEventListener('keydown', onKey);
		};
	}, [onClose, anchorRef]);

	return createPortal(
		<div
			ref={panelRef}
			role="menu"
			style={pos}
			className={cn(
				'border-border bg-background fixed z-50 min-w-[180px] rounded-lg border p-1 shadow-lg',
				className,
			)}
		>
			{children}
		</div>,
		document.body,
	);
}

/** Thin inset hairline used to group menu items into sections. */
export function MenuSeparator() {
	return <div className="bg-border/60 mx-1 my-1 h-px" aria-hidden="true" />;
}

/**
 * Canonical menu-item className. Apply to whichever element you need
 * (`<AppLink>`, `<Button>`, `<a>`) and set `role="menuitem"` on it.
 *
 * Pass `active=true` for the highlighted/current state.
 */
export function menuItemClass(active = false): string {
	return cn(
		'flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors duration-150',
		active
			? 'text-foreground bg-muted'
			: 'text-muted-foreground hover:bg-muted hover:text-foreground',
	);
}
