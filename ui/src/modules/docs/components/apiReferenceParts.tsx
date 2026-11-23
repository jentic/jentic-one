import { useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

/**
 * A collapsible row whose body is only mounted while open — so heavy schema
 * trees and security-scheme details aren't rendered until the reader asks for
 * them.
 */
export function Disclosure({
	summary,
	defaultOpen = false,
	children,
}: {
	summary: ReactNode;
	defaultOpen?: boolean;
	children: ReactNode;
}) {
	const [open, setOpen] = useState(defaultOpen);
	return (
		<div className="border-border/40 bg-card/30 overflow-hidden rounded-lg border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="hover:bg-muted/30 flex w-full items-start gap-2 px-3 py-2 text-left transition-colors"
			>
				<ChevronRight
					className={cn(
						'text-foreground-faint mt-0.5 h-3.5 w-3.5 shrink-0 transition-transform',
						open && 'rotate-90',
					)}
					aria-hidden="true"
				/>
				<span className="min-w-0 flex-1">{summary}</span>
			</button>
			{open && <div className="border-border/40 border-t px-3 py-3">{children}</div>}
		</div>
	);
}

export function SectionLabel({ children, count }: { children: ReactNode; count?: number }) {
	return (
		<h4 className="text-foreground-sub mb-2 flex items-baseline gap-2 text-[11px] font-semibold tracking-wider uppercase">
			{children}
			{count != null && (
				<span className="text-foreground-faint font-mono text-[10px] normal-case">
					{count}
				</span>
			)}
		</h4>
	);
}
