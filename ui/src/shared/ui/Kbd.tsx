import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

interface KbdProps {
	children: ReactNode;
	size?: 'sm' | 'md';
	variant?: 'outline' | 'solid';
	className?: string;
}

/**
 * Stylised `<kbd>` for rendering a single keyboard key.
 *
 * Composes inside a `<span>` of `<Kbd>` children when a shortcut needs
 * multiple keys (e.g. `<Kbd>⌘</Kbd><Kbd>K</Kbd>`). Each key is its own
 * pill so wrapping behaviour is predictable on narrow viewports.
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
