import type { ReactNode } from 'react';

/** A monochrome icon medallion for a card or section heading. */
export function CardHeaderIcon({ children }: { children: ReactNode }) {
	return (
		<span className="bg-surface-tonal text-foreground-sub flex h-7 w-7 shrink-0 items-center justify-center rounded-md">
			{children}
		</span>
	);
}
