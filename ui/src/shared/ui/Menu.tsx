import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
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
 *  - {@link MenuPanel} — the absolutely-positioned popover shell.
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
