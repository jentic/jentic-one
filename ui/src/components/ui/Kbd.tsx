import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface KbdProps {
	children: ReactNode;
	/**
	 * Visual size. `sm` (default) is the compact pill we render inline
	 * inside inputs and small affordances; `md` is for shortcut help
	 * surfaces (dialog rows, the bottom shortcuts bar) where the keys
	 * need to read clearly at a glance.
	 */
	size?: 'sm' | 'md';
	/**
	 * Visual variant. `outline` (default) is a thin-bordered chip that
	 * sits well on dark backgrounds without competing for attention —
	 * used inline (e.g. ⌘K hint inside the search box). `solid` is a
	 * filled chip used when the kbd is the focal point of its row
	 * (shortcuts dialog, shortcuts bar) so the keys read as
	 * "pressable".
	 */
	variant?: 'outline' | 'solid';
	className?: string;
}

/**
 * Stylised `<kbd>` for rendering a single keyboard key.
 *
 * Composes inside a `<span>` of `<Kbd>` children when a shortcut needs
 * multiple keys (e.g. `<Kbd>⌘</Kbd><Kbd>K</Kbd>`). Each key is its own
 * pill so wrapping behaviour is predictable on narrow viewports.
 *
 * Why a primitive at all: we had three near-identical `<kbd>` blobs
 * scattered across `DiscoverPage` (search hint, shortcuts dialog row)
 * and the planned bottom shortcut bar. Centralising the styling here
 * keeps them visually consistent and means future themes (light mode,
 * high-contrast, etc.) only need to be threaded through one place.
 */
export function Kbd({ children, size = 'sm', variant = 'outline', className }: KbdProps) {
	return (
		<kbd
			className={cn(
				'inline-flex items-center justify-center rounded font-mono leading-none',
				size === 'sm'
					? 'min-w-[1.25rem] px-1.5 py-0.5 text-[10px]'
					: 'min-w-[1.5rem] px-1.5 py-0.5 text-[11px]',
				variant === 'outline'
					? 'text-muted-foreground border border-current/30'
					: 'border-border/60 bg-muted text-foreground border',
				className,
			)}
		>
			{children}
		</kbd>
	);
}
