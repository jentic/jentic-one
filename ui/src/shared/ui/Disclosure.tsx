import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export interface DisclosureProps {
	/** Always-visible summary line that toggles the body open/closed. */
	summary: ReactNode;
	/** Open on first render. Defaults to collapsed. */
	defaultOpen?: boolean;
	/** Extra classes on the `<details>` wrapper. */
	className?: string;
	/** Extra classes on the revealed body. */
	bodyClassName?: string;
	children: ReactNode;
}

/**
 * Disclosure — a lightweight, keyboard-operable "hide behind a click" section
 * built on native `<details>`/`<summary>`, so it toggles with Enter/Space and
 * is announced correctly without any JS state. Use it to move secondary detail
 * off the always-visible surface while keeping it one click away.
 */
export function Disclosure({
	summary,
	defaultOpen = false,
	className,
	bodyClassName,
	children,
}: DisclosureProps) {
	return (
		<details className={cn('group', className)} open={defaultOpen}>
			<summary className="text-muted-foreground hover:text-foreground focus-visible:ring-primary/50 flex cursor-pointer list-none items-center gap-1.5 rounded text-xs font-medium focus-visible:ring-2 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
				<ChevronDown
					aria-hidden="true"
					className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-180"
				/>
				{summary}
			</summary>
			<div
				className={cn('text-muted-foreground mt-2 text-xs leading-relaxed', bodyClassName)}
			>
				{children}
			</div>
		</details>
	);
}
