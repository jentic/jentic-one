/**
 * CollapsibleSection — a minimal module-local disclosure used by the drill-in
 * drawers to keep secondary detail (request/response, timeline, governance)
 * out of the way. Closed by default so the drawer stays clean on open; a
 * chevron rotates as it opens. No shared Disclosure primitive exists, so this
 * is intentionally tiny and unopinionated.
 */
import { useId, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

interface CollapsibleSectionProps {
	title: string;
	icon?: React.ReactNode;
	/** Small right-aligned hint (e.g. a count or a status word). */
	meta?: React.ReactNode;
	defaultOpen?: boolean;
	children: React.ReactNode;
}

export function CollapsibleSection({
	title,
	icon,
	meta,
	defaultOpen = false,
	children,
}: CollapsibleSectionProps) {
	const [open, setOpen] = useState(defaultOpen);
	const panelId = useId();

	return (
		<div className="border-border/50 overflow-hidden rounded-lg border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				aria-controls={panelId}
				className="hover:bg-muted/40 focus-visible:ring-ring flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				<ChevronRight
					className={cn(
						'text-muted-foreground h-4 w-4 shrink-0 transition-transform',
						open && 'rotate-90',
					)}
				/>
				{icon && <span className="text-muted-foreground shrink-0">{icon}</span>}
				<span className="text-foreground flex-1 text-xs font-semibold tracking-wide uppercase">
					{title}
				</span>
				{meta != null && (
					<span className="text-muted-foreground shrink-0 text-[11px]">{meta}</span>
				)}
			</button>
			{open && (
				<div id={panelId} className="border-border/50 border-t px-3 py-3">
					{children}
				</div>
			)}
		</div>
	);
}
