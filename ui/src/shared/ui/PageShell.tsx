import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/utils';

/**
 * Width variants for in-Layout pages. `PageShell` owns both the
 * horizontal gutter (via the `--spacing-page-gutter` theme token →
 * `px-page-gutter` utility) and the vertical rhythm.
 *
 * - `wide` (default): dashboards, lists, tables. No max-width.
 * - `reading`: detail pages with long prose / sequential sections.
 * - `form`: single-column forms.
 */
type PageWidth = 'wide' | 'reading' | 'form';

const WIDTH_CLASS: Record<PageWidth, string> = {
	wide: '',
	reading: 'mx-auto max-w-4xl',
	form: 'mx-auto max-w-2xl',
};

export interface PageShellProps {
	children: ReactNode;
	/** Content max-width preset. Defaults to `wide`. */
	width?: PageWidth;
	/** Tailwind class controlling vertical rhythm between top-level children. Defaults to `space-y-6`. */
	spacing?: string;
	/** Extra classes appended to the outer wrapper. */
	className?: string;
}

/**
 * Standard page container for routes mounted under the main `Layout`.
 *
 * Picks a sensible max-width, owns the shared horizontal gutter, and
 * applies a consistent vertical rhythm so every page lays out the same
 * way.
 */
export function PageShell({
	children,
	width = 'wide',
	spacing = 'space-y-6',
	className,
}: PageShellProps) {
	return (
		<div
			className={cn(
				'px-page-gutter w-full overflow-x-clip py-6',
				WIDTH_CLASS[width],
				spacing,
				className,
			)}
		>
			{children}
		</div>
	);
}
